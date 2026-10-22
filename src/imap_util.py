"""Thin, testable wrapper around imaplib for the router.

Folder names are handled in their encoded (modified UTF-7) form everywhere,
because the folders use Portuguese characters ("Não-cliente", "Alterações").
"""
from __future__ import annotations

import base64
import email
import email.policy
import imaplib
import re
import ssl

imaplib.Commands.setdefault("MOVE", ("SELECTED",))   # RFC 6851, unknown to older imaplib

TRASH_NAMES = {"trash", "deleted messages", "deleted items", "lixo", "reciclagem", "itens eliminados"}
_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) (?:"(?P<delim>[^"]*)"|NIL) (?P<name>.+)$')


class IMAPError(RuntimeError):
    pass


def encode_mutf7(s: str) -> str:
    out, run = [], []

    def flush():
        if run:
            b64 = base64.b64encode("".join(run).encode("utf-16-be")).decode().rstrip("=")
            out.append("&" + b64.replace("/", ",") + "-")
            run.clear()

    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    flush()
    return "".join(out)


def decode_mutf7(s: str) -> str:
    def dec(m):
        b64 = m.group(1)
        if not b64:
            return "&"
        b64 = b64.replace(",", "/") + "=" * (-len(b64) % 4)
        return base64.b64decode(b64).decode("utf-16-be")
    return re.sub(r"&([A-Za-z0-9+,]*)-", dec, s)


def imap_date(iso_day: str) -> str:
    y, m, d = iso_day[:10].split("-")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{int(d)}-{months[int(m) - 1]}-{y}"


# Printable ASCII without spaces, quotes or backslashes.
SAFE_MESSAGE_ID = re.compile(r"[!#-\[\]-~]+")


def quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_namespace(data) -> tuple[str, str]:
    raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    m = re.match(r'\(\("([^"]*)" (?:"([^"]*)"|NIL)\)', raw)
    return (m.group(1), m.group(2) or ".") if m else ("", ".")


def parse_list_line(line) -> tuple[str, str]:
    s = line.decode() if isinstance(line, bytes) else line
    m = _LIST_RE.match(s)
    if not m:
        raise IMAPError(f"unexpected LIST line: {s!r}")
    name = m.group("name")
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return m.group("flags"), name


class Mailbox:
    INBOX = "INBOX"

    def __init__(self, conn):
        self.c = conn
        caps = conn.capabilities
        # imaplib keeps the pre-login capability list; the server only reveals
        # MOVE, UIDPLUS, QUOTA and NAMESPACE after authentication, so ask again.
        if hasattr(conn, "capability"):
            typ, data = conn.capability()
            if typ == "OK" and data and data[0]:
                caps = data[0].split()
        self.caps = {(x.decode() if isinstance(x, bytes) else x).upper() for x in caps}
        self.prefix, self.delim = "", "."
        if "NAMESPACE" in self.caps:
            typ, data = conn.namespace()
            if typ == "OK":
                self.prefix, self.delim = parse_namespace(data)

    @classmethod
    def connect(cls, host: str, port: int, user: str, password: str) -> "Mailbox":
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context())
        conn.login(user, password)
        return cls(conn)

    def _ok(self, res, what: str):
        typ, data = res
        if typ != "OK":
            raise IMAPError(f"{what} failed: {data!r}")
        return data

    def folders(self) -> dict[str, str]:
        """Encoded folder name -> LIST flags."""
        out = {}
        for line in self._ok(self.c.list(), "LIST"):
            if line:
                flags, name = parse_list_line(line)
                out[name] = flags
        return out

    def ensure_folders(self, names: dict[str, str]) -> dict[str, str]:
        """Create any missing folder; return folder id -> encoded server name."""
        existing, result = self.folders(), {}
        for fid, name in names.items():
            enc = encode_mutf7(self.prefix + name)
            if enc not in existing:
                self._ok(self.c.create(quote(enc)), f"CREATE {name}")
                self.c.subscribe(quote(enc))   # so Apple Mail and the phone show it
            result[fid] = enc
        return result

    def encoded(self, names: dict[str, str]) -> dict[str, str]:
        """Folder id -> encoded server name, without creating anything."""
        return {fid: encode_mutf7(self.prefix + name) for fid, name in names.items()}

    def select(self, enc: str, readonly: bool = False) -> None:
        self._ok(self.c.select(quote(enc), readonly), f"SELECT {enc}")

    def append(self, enc: str, flags: str, internal_date: str, raw: bytes) -> None:
        self._ok(self.c.append(quote(enc), flags, internal_date, raw), f"APPEND {enc}")

    def uids(self, since: str | None = None) -> list[bytes]:
        """All UIDs, or those whose arrival date is on/after `since` (YYYY-MM-DD)."""
        crit = ("SINCE", imap_date(since)) if since else ("ALL",)
        data = self._ok(self.c.uid("SEARCH", None, *crit), "SEARCH")
        return data[0].split() if data and data[0] else []

    def flagged(self) -> list[bytes]:
        data = self._ok(self.c.uid("SEARCH", None, "FLAGGED"), "SEARCH")
        return data[0].split() if data and data[0] else []

    def fetch(self, uid) -> bytes:
        # BODY.PEEK so the router never marks anything as read.
        for part in self._ok(self.c.uid("FETCH", uid, "(BODY.PEEK[])"), "FETCH"):
            if isinstance(part, tuple):
                return part[1]
        raise IMAPError(f"no body for uid {uid!r}")

    def flag(self, uid) -> None:
        self._ok(self.c.uid("STORE", uid, "+FLAGS", "(\\Flagged)"), "STORE")

    def unflag(self, uid) -> None:
        self._ok(self.c.uid("STORE", uid, "-FLAGS", "(\\Flagged)"), "STORE")

    def move(self, uid, dest_enc: str) -> None:
        if "MOVE" in self.caps:
            self._ok(self.c.uid("MOVE", uid, quote(dest_enc)), "MOVE")
            return
        self._ok(self.c.uid("COPY", uid, quote(dest_enc)), "COPY")
        self._ok(self.c.uid("STORE", uid, "+FLAGS", "(\\Deleted)"), "STORE")
        if "UIDPLUS" in self.caps:   # otherwise leave it marked, never a blanket EXPUNGE
            self._ok(self.c.uid("EXPUNGE", uid), "UID EXPUNGE")

    def delete(self, uid) -> None:
        """Remove one message for good (a replaced or sent draft); needs UIDPLUS, never a blanket EXPUNGE."""
        if "UIDPLUS" not in self.caps:
            raise IMAPError("UIDPLUS needed to delete a single message")
        self._ok(self.c.uid("STORE", uid, "+FLAGS", "(\\Deleted)"), "STORE")
        self._ok(self.c.uid("EXPUNGE", uid), "UID EXPUNGE")

    def find(self, message_id: str) -> list[bytes]:
        """UIDs whose Message-ID is exactly `message_id`. The ID comes from the email itself:
        SEARCH HEADER matches substrings, and line breaks or non-ASCII would reach the server."""
        if not SAFE_MESSAGE_ID.fullmatch(message_id):
            return []
        data = self._ok(self.c.uid("SEARCH", None, "HEADER", "Message-ID", quote(message_id)), "SEARCH")
        uids = data[0].split() if data and data[0] else []
        return [u for u in uids if self.message_id(u) == message_id]

    def message_id(self, uid) -> str:
        for part in self._ok(self.c.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"), "FETCH"):
            if isinstance(part, tuple):
                msg = email.message_from_bytes(part[1], policy=email.policy.default)
                return str(msg.get("Message-ID", "")).strip()
        return ""

    def headers(self, uid, names: str = "MESSAGE-ID IN-REPLY-TO REFERENCES DATE SUBJECT"):
        """Only the named header fields, fetched with BODY.PEEK."""
        for part in self._ok(self.c.uid("FETCH", uid, f"(BODY.PEEK[HEADER.FIELDS ({names})])"), "FETCH"):
            if isinstance(part, tuple):
                return email.message_from_bytes(part[1], policy=email.policy.default)
        return email.message_from_bytes(b"", policy=email.policy.default)

    def trash(self) -> str | None:
        folders = self.folders()
        for name, flags in folders.items():
            if "\\Trash" in flags:
                return name
        for name in folders:
            if decode_mutf7(name).split(self.delim)[-1].lower() in TRASH_NAMES:
                return name
        return None

    def counts(self) -> list[tuple[str, int]]:
        out = []
        for enc in sorted(self.folders()):
            typ, data = self.c.status(quote(enc), "(MESSAGES)")
            m = re.search(rb"MESSAGES (\d+)", data[0] or b"") if typ == "OK" else None
            out.append((decode_mutf7(enc), int(m.group(1)) if m else -1))
        return out

    def quota(self):
        if "QUOTA" not in self.caps:
            return None
        typ, data = self.c.getquotaroot("INBOX")
        return data if typ == "OK" else None

    def logout(self) -> None:
        try:
            self.c.logout()
        except Exception:
            pass
