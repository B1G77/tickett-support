#!/usr/bin/env python3
"""Stage 1 - extract Apple Mail .emlx messages into JSONL.

Reads the LeBillet support INBOX strictly read-only and writes one JSON
object per message to data/01_extracted.jsonl.

No cleaning, no thread grouping, no redaction: those are stages 2-4.
Attachment bodies are never decoded - only filename, type and an
approximate size are recorded.
"""
from __future__ import annotations

import email
import email.policy
import json
import re
import sys
from collections.abc import Iterator
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

MAILBOX = Path.home() / "Library/Mail/V10/E018D14D-2946-49F7-8D86-58167B3BCE68/INBOX.mbox"
OUT = Path(__file__).resolve().parent.parent / "data" / "01_extracted.jsonl"
BODY_CAP = 100_000

# Rough stopword fingerprints. Portuguese and Spanish overlap heavily, so a
# thin margin between the top two scores is reported as "unclear".
LANG_WORDS = {
    "pt": {"nao", "obrigado", "obrigada", "bilhete", "bilhetes", "uma", "ola",
           "gostaria", "poderia", "reembolso", "compra", "evento", "meu", "sou"},
    "en": {"the", "and", "you", "for", "have", "would", "ticket", "tickets",
           "hello", "thanks", "please", "order", "refund", "my", "was"},
    "fr": {"bonjour", "merci", "billet", "billets", "pour", "avec", "je",
           "vous", "commande", "remboursement", "mon", "suis"},
    "nl": {"hallo", "bedankt", "kaartjes", "kaarten", "voor", "met", "een",
           "ik", "graag", "bestelling", "mijn", "heb"},
    "de": {"hallo", "danke", "karten", "fuer", "mit", "eine", "ich", "gerne",
           "bestellung", "mein", "habe", "sehr"},
    "es": {"hola", "gracias", "entrada", "entradas", "quisiera", "pedido",
           "reembolso", "mi", "soy", "estoy"},
}
_WORD_RE = re.compile(r"[a-zà-ÿ]{2,}")
_WS_RE = re.compile(r"[ \t\xa0]+")
_NL_RE = re.compile(r"\n{3,}")


class _HTMLText(HTMLParser):
    """Minimal HTML-to-text: drops non-content tags, keeps line structure."""

    # Void tags must never appear here: they have no end tag, so the skip
    # counter would never reset and the rest of the message would be lost.
    SKIP = {"script", "style", "title"}
    BREAK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass
    return "".join(parser.parts)


def tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NL_RE.sub("\n\n", text).strip()


def guess_language(text: str) -> tuple[str, float]:
    words = _WORD_RE.findall(text[:4000].lower())
    if len(words) < 8:
        return "unknown", 0.0
    counts = {lang: sum(w in vocab for w in words) for lang, vocab in LANG_WORDS.items()}
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    best, best_n = ranked[0]
    second_n = ranked[1][1]
    if best_n == 0:
        return "unknown", 0.0
    margin = (best_n - second_n) / best_n
    return (best if margin >= 0.25 else "unclear"), round(margin, 2)


def iter_emlx(root: Path) -> Iterator[Path]:
    yield from sorted(root.rglob("*.emlx"))


def read_emlx(path: Path) -> bytes:
    """An .emlx file is: byte-count line, the RFC822 message, then a plist."""
    raw = path.read_bytes()
    nl = raw.find(b"\n")
    if nl == -1:
        raise ValueError("no length prefix")
    length = int(raw[:nl].strip())
    return raw[nl + 1: nl + 1 + length]


def split_parts(msg) -> tuple[list[str], list[str], list[dict], list[str]]:
    plain: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []
    flags: list[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        disposition = part.get_content_disposition() or ""

        if not ctype.startswith("text/") or disposition == "attachment":
            payload = part.get_payload(decode=False)
            approx = int(len(payload) * 0.75) if isinstance(payload, str) else 0
            attachments.append({
                "filename": part.get_filename(),
                "content_type": ctype,
                "approx_bytes": approx,
            })
            continue

        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
            flags.append("charset_fallback")

        if ctype == "text/plain":
            plain.append(content)
        elif ctype == "text/html":
            html_parts.append(content)

    return plain, html_parts, attachments, flags


def build_record(path: Path, root: Path) -> dict:
    return record_from_bytes(read_emlx(path), str(path.relative_to(root)))


def record_from_bytes(raw: bytes, rec_id: str) -> dict:
    """Parse one RFC822 message - from an .emlx file or straight from IMAP."""
    flags: list[str] = []
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    plain, html_parts, attachments, part_flags = split_parts(msg)
    flags.extend(part_flags)

    if plain:
        body, source = tidy("\n".join(plain)), "plain"
    elif html_parts:
        body, source = tidy(html_to_text("\n".join(html_parts))), "html"
    else:
        body, source = "", "none"
        flags.append("empty_body")

    if len(body) > BODY_CAP:
        body = body[:BODY_CAP]
        flags.append("body_truncated")

    def header(name: str) -> str:
        try:
            value = msg.get(name)
            return str(value).strip() if value else ""
        except Exception:
            flags.append(f"bad_header:{name}")
            return ""

    sender = getaddresses([header("From")] or [""])
    from_name, from_addr = sender[0] if sender else ("", "")
    from_addr = from_addr.lower()

    date_raw = header("Date")
    date_iso = ""
    if date_raw:
        try:
            date_iso = parsedate_to_datetime(date_raw).astimezone().isoformat()
        except Exception:
            flags.append("bad_date")

    lang, margin = guess_language(body)

    spam_flag = header("X-Spam-Flag").upper() == "YES"
    try:
        spam_score = float(header("X-Spam-Score") or "nan")
    except ValueError:
        spam_score = None
    auth = header("Authentication-Results").lower()

    return {
        "id": rec_id,
        "message_id": header("Message-ID"),
        "in_reply_to": header("In-Reply-To"),
        "references": header("References").split(),
        "from_name": from_name,
        "from_email": from_addr,
        "from_domain": from_addr.rpartition("@")[2],
        "to": [a.lower() for _, a in getaddresses([header("To")]) if a],
        "subject": header("Subject"),
        "date": date_iso,
        "date_raw": date_raw,
        "body_text": body,
        "body_source": source,
        "body_chars": len(body),
        "attachments": attachments,
        "attachment_count": len(attachments),
        "lang_guess": lang,
        "lang_margin": margin,
        # Triage signals. The mail server runs SpamAssassin, so its verdict is
        # already on 98% of messages - far better than a hand-written domain list.
        "spam_flag": spam_flag,
        "spam_score": spam_score,
        "dmarc_fail": "dmarc=fail" in auth,
        "spf_fail": "spf=fail" in auth or "spf=softfail" in auth,
        "reply_to": header("Reply-To").lower(),
        "return_path": header("Return-Path").lower(),
        "list_unsubscribe": bool(header("List-Unsubscribe")),
        "precedence": header("Precedence").lower(),
        "auto_submitted": header("Auto-Submitted").lower(),
        "flags": flags,
    }


def main() -> int:
    if not MAILBOX.is_dir():
        print(f"mailbox not found: {MAILBOX}", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    ok = failed = 0

    with OUT.open("w", encoding="utf-8") as out:
        for n, path in enumerate(iter_emlx(MAILBOX), 1):
            try:
                record = build_record(path, MAILBOX)
                ok += 1
            except Exception as exc:
                record = {
                    "id": str(path.relative_to(MAILBOX)),
                    "flags": ["extract_failed"],
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
                failed += 1
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            if n % 500 == 0:
                print(f"  {n} processed...", file=sys.stderr, flush=True)

    print(f"\nextracted {ok}, failed {failed} -> {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
