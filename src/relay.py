"""The customer's address in relayed mail (a website form, or a partner forwarding a customer's email).

The From of such mail is the form mailer or the partner; the customer's address is only in the text: a form
field ("E-mail: ...", the value on the same line or a line or two below the label) or the original sender in
the forwarded header block ("From:", "De:", "De :", "Von:"). The block after a forward marker ("Forwarded
message", "Mensagem reencaminhada", "Début du message réexpédié", ...) is preferred over anything above it,
such as the partner's own signature. Outside such a block a "From:/De:" line counts only with the address in
<...> or [mailto:...] on the same line. It stays on the server: it is never sent to Claude.
"""
from __future__ import annotations

import re

_ADDR = r"[A-Za-z0-9_%+'-]+(?:\.[A-Za-z0-9_%+'-]+)*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
_EXACT = re.compile(rf"{_ADDR}")
_MARKER = re.compile(r"(?im)^[ \t>*-]*(?:begin forwarded message|forwarded message|forwarded|original message|"
                     r"mensagem (?:re)?encaminhada|d[ée]but du message r[ée]exp[ée]di[ée]|message transf[ée]r[ée]|"
                     r"weitergeleitete nachricht|weitergeleitet|urspr[uü]ngliche nachricht)[ \t:*-]*$")
_LABEL = (r"e-?mail|correio eletr[óo]nico|endere[çc]o de e-?mail|email address|courriel|adresse e-?mail")
# a form field: the label (and up to two more words: "Email cliente:"), then the address on the same line, or the
# bare label with the address a line or two below (blank lines allowed)
_FIELD = re.compile(rf"(?im)^[ \t>]*(?:{_LABEL})(?:(?:[ \t]+[^\W\d_]+){{0,2}}[ \t]*:[ \t]*|[ \t]*\n(?:[ \t>]*\n){{0,2}}[ \t>]*)"
                    rf"(?:([^\n@<]*?)[ \t]*<)?({_ADDR})>?[ \t]*$")
_NAME = re.compile(r"(?im)^[ \t>]*(?:nome|name|nom|nome completo|full name)[ \t]*(?::[ \t]*|\n(?:[ \t>]*\n){0,2}[ \t>]*)"
                   r"([^\n@:]{1,80})$")
_HEAD = r"^[ \t>]*(?:from|de|von|van|da)[ \t]*:[ \t]*"
# inside a forwarded block: "From: Name <x@y>", "From: x@y", "From: Name [mailto:x@y]"
_HEADER_IN = re.compile(rf"(?im){_HEAD}(?:\"?([^\n@<\[\"]*?)\"?[ \t]*(?:<|\[mailto:))?({_ADDR})[>\]]?")
# anywhere else: the address in <...> or [mailto:...], or bare at the end after a plain name (no comma: not prose)
_HEADER_OUT = re.compile(rf"(?im){_HEAD}\"?([^\n@<\[\"]*?)\"?[ \t]*(?:<|\[mailto:)({_ADDR})[>\]]")
_HEADER_BARE = re.compile(rf"(?im){_HEAD}(?:([^\W\d_][^\W\d_' .-]{{0,60}}?)[ \t]+)?({_ADDR})[ \t]*$")
# Outlook on the web: a line of underscores, then the header lines
_OUTLOOK = re.compile(rf"(?im)^[ \t]*_{{5,}}[ \t]*\n(?:[ \t]*\n)?(?={_HEAD})")
# a quote attribution: "On Mon, Ana <ana@x> wrote:", "Em seg., ..., Ana <ana@x> escreveu:"
_WROTE = r"(?:wrote|escreveu|a [ée]crit|schrieb|schreef)"
_ATTRIB = re.compile(rf"(?im)^[ \t>]*(?:on|em|le|am|op)\b[^\n]*,[ \t]*\"?([^\n<\",]*?)\"?[ \t]*<({_ADDR})>[^\n]*?{_WROTE}")
_ATTRIB_LINE = re.compile(rf"(?im)^[ \t>]*(?:on|em|le|am|op)\b[^\n]*{_WROTE}[ \t]*:?[ \t]*$")
_QUOTED_HEAD = re.compile(r"(?im)^[ \t]*>[ \t>]*(?:from|de|von|van|da|to|para|à|an|sent|enviado|date|data)[ \t]*:[^\n]*$")
_NO_REPLY = re.compile(r"(?i)^(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|notify|notifications?|bounces?|"
                       r"mailer[-_.]?daemon|postmaster|wordpress|forms?|webform|formul[aá]rio)(?:[+._-].*)?@")
_INTERNAL = re.compile(r"(?i)@(?:[a-z0-9-]+\.)*(?:tickett\.com|mytickett\.com|lebillet\.(?:eu|pt|com\.br))$")
WEBMAIL = {"gmail.com", "googlemail.com", "hotmail.com", "hotmail.fr", "hotmail.pt", "outlook.com", "outlook.pt",
           "outlook.fr", "live.com", "live.fr", "msn.com", "yahoo.com", "yahoo.fr", "ymail.com", "sapo.pt",
           "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.de", "gmx.net",
           "web.de", "orange.fr", "free.fr", "wanadoo.fr", "laposte.net", "netcabo.pt", "clix.pt", "iol.pt"}


def _domain(addr: str) -> str:
    return addr.rpartition("@")[2].lower()


def _ok(addr: str, relayer: str) -> bool:
    """Not the relayer, not on the relayer's own (non-webmail) domain, not internal, not a no-reply mailer."""
    a, r = addr.lower(), (relayer or "").strip().lower()
    if a == r or _INTERNAL.search(a) or _NO_REPLY.match(a):
        return False
    return not (r and _domain(a) == _domain(r) and _domain(r) not in WEBMAIL)


def _name(raw: str | None) -> str:
    n = (raw or "").strip().strip("\"'").strip()
    return "" if "@" in n else n


def _hits(text: str, headers) -> list[tuple[int, str, str]]:
    """(position, address, name next to it) for every form field, header line and quote attribution."""
    names = [(m.start(), m.group(1).strip()) for m in _NAME.finditer(text)]

    def nearest(pos: int) -> str:   # the form's Nome field closest to its E-mail field
        return min(names, key=lambda n: abs(n[0] - pos))[1] if names else ""
    out = [(m.start(), m.group(2), _name(m.group(1)) or nearest(m.start())) for m in _FIELD.finditer(text)]
    for header in (*headers, _ATTRIB):
        out += [(m.start(), m.group(2), _name(m.group(1))) for m in header.finditer(text)]
    return sorted(out)


def _block_start(text: str) -> int | None:
    """Where the forwarded block begins: after a forward marker or an Outlook separator; None without one."""
    ends = [m.end() for m in (_MARKER.search(text), _OUTLOOK.search(text)) if m]
    return min(ends) if ends else None


def customer(text: str, relayer: str) -> tuple[str, str]:
    """(the customer's address, the name next to it) in relayed mail; ("", "") when none is found. The forwarded
    block first; then form fields, header lines and quote attributions anywhere; last, any acceptable address
    in the text (the person confirms it in Para: before anything is sent)."""
    text = text or ""
    start = _block_start(text)
    tries = ([(text[start:], (_HEADER_IN,))] if start is not None else []) + [(text, (_HEADER_OUT, _HEADER_BARE))]
    for part, headers in tries:
        for _, addr, name in _hits(part, headers):
            if _ok(addr, relayer):
                return addr, name
    return next(((m.group(0), "") for m in _EXACT.finditer(text) if _ok(m.group(0), relayer)), ("", ""))


def _robot(addr: str) -> bool:
    return bool(_INTERNAL.search(addr) or _NO_REPLY.match(addr))


_TO = re.compile(r"(?im)^[ \t>]*(?:to|para|à|a|an|aan)[ \t]*:[^\n]*")
_ORIGIN = re.compile(rf"(?im){_HEAD}[^\n]*|^[ \t>]*(?:on|em|le|am|op)\b[^\n]*{_WROTE}")


def own_forward(text: str, sender: str) -> bool:
    """Positive evidence that the sender forwarded their own email from Tickett (or a robot), so the sender is the
    customer. All of: the sender is on public webmail and the block's To:/Para: line names them; a forwarded block (forward marker, Outlook separator, quoted header lines or a quote
    attribution); its original sender (the block's first From:/De: line or attribution) is a tickett, lebillet or
    no-reply address; every address in it is one of those or the sender's own; and no customer address is found
    anywhere in the text. Anything else stays relayed."""
    text, me = text or "", (sender or "").strip().lower()
    start = _block_start(text)
    if start is not None:
        part = text[start:]
    else:
        part = "\n".join(m.group(0) for r in (_QUOTED_HEAD, _ATTRIB_LINE) for m in r.finditer(text))
    found = _EXACT.findall(part)
    origin = _ORIGIN.search(part)
    origin_addrs = _EXACT.findall(origin.group(0)) if origin else []
    to_me = any(me in (a.lower() for a in _EXACT.findall(m.group(0))) for m in _TO.finditer(part))
    return _domain(me) in WEBMAIL and to_me and bool(found) and any(_robot(a) for a in found) \
        and all(a.lower() == me or _robot(a) for a in found) \
        and bool(origin_addrs) and all(_robot(a) for a in origin_addrs) \
        and customer_address(text, sender) == ""


def customer_address(text: str, relayer: str) -> str:
    """The customer's address (see customer()): never the relayer, internal or a no-reply / form-mailer
    address; "" when there is none."""
    return customer(text, relayer)[0]


def valid(addr: str, relayer: str) -> bool:
    """Exactly one plain address, not the relayer's, not internal, not no-reply: where a relayed reply may go."""
    a = (addr or "").strip(" \t")
    return bool(_EXACT.fullmatch(a)) and _ok(a, relayer)
