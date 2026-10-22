#!/usr/bin/env python3
"""Stage 4 - redact cases before any text leaves this machine.

Design (agreed with the review council):
- Stdlib regex, confirmed by checksums where one exists (IBAN mod-97,
  card Luhn). No NER model: NER tags artists as PERSON and would erase the
  event names we deliberately keep.
- Names come from each case's own headers (display names, email local
  parts) plus positional rules (greetings, sign-offs, "o meu nome e").
- Typed placeholders, never deletion: "[IBAN]" is itself a refund signal.
- Output is built from an explicit allowlist of fields. Raw subject,
  addresses and attachment filenames never reach the output file.
- A fail-closed leak scan marks any case that still looks like it holds
  PII; those cases are not eligible to be sent.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict

VERSION = "redact-6.1"   # rules only; src/redactor.py applies them

INTERNAL = ("lebillet.eu", "lebillet.com.br", "lebillet.pt")
# Tokens that appear in display names but are never a person.
NOT_NAMES = {
    "lebillet", "le", "billet", "suporte", "support", "atendimento", "info",
    "geral", "logistica", "equipa", "team", "festival", "camara", "municipal",
    "the", "and", "dos", "das", "de", "da", "do", "e", "via", "mail", "email",
    # Function words and mail providers: a display name such as
    # "maria@gmail.com" or "Com Amor" must not turn "com" into a name.
    "com", "para", "por", "que", "uma", "seu", "sua", "nos", "nous", "vous",
    "les", "des", "pour", "avec", "for", "you", "con", "los", "las", "del",
    "nao", "sim", "ola", "bom", "boa", "dia", "gmail", "hotmail", "outlook",
    "yahoo", "icloud", "live", "sapo", "msn", "contact", "contacto", "reservas",
    "noreply", "hello", "admin",
}
# A display name containing any of these belongs to an organisation, whose
# words ("Crato", "Festival") are routing signal, not personal data.
ORG_WORDS = {
    "camara", "municipal", "municipio", "junta", "freguesia", "festival",
    "associacao", "association", "clube", "club", "lda", "ltda", "sa", "hotel",
    "universidade", "university", "escola", "school", "servicos", "services",
    "departamento", "department", "comercial", "eventos", "events", "producoes",
    "productions", "agencia", "agency", "grupo", "group", "company", "iucn",
    "fundacao", "foundation", "teatro", "theatre", "museu", "cooperativa",
    "instituto", "institute", "centro", "center", "centre", "bilheteira",
    "tickets", "ticketline", "records", "music", "musica", "entertainment",
    "media", "gmbh", "sarl", "inc", "ltd", "llc", "office", "admin",
}
TITLES = {"dr", "dra", "sr", "sra", "eng", "prof", "mr", "mrs", "ms", "mme", "mlle"}
KEEP_MIN_CUSTOMERS = 5  # phrase seen across this many customers = public name

_ACCENTS = {"a": "aáàâãä", "e": "eéèêë", "i": "iíìîï", "o": "oóòôõö",
            "u": "uúùûü", "c": "cç", "n": "nñ"}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower())
                   if not unicodedata.combining(c))


def accent_rx(token: str) -> str:
    return "".join(f"[{_ACCENTS[c]}{_ACCENTS[c].upper()}]" if c in _ACCENTS
                   else re.escape(c) for c in fold(token))


def iban_ok(raw: str) -> bool:
    s = re.sub(r"\s", "", raw).upper()
    if not (15 <= len(s) <= 34):
        return False
    digits = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return int(digits) % 97 == 1


def luhn_ok(raw: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", raw)][::-1]
    return 13 <= len(d) <= 19 and sum(
        x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2)
        for i, x in enumerate(d)) % 10 == 0


# --- structured identifiers, applied in this order -----------------------
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\]\)\"']+", re.I)
_EMAIL = re.compile(r"(?:mailto:)?[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)
_EMAIL_OBF = re.compile(r"\b[\w.+-]{2,}\s*(?:\(at\)|\[at\]| arroba | at )\s*[\w-]+\s*(?:\.|dot| ponto )\s*[a-z]{2,}\b", re.I)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_CARD4 = re.compile(r"\b(terminad[oa] em|final|ending in|se terminant par|terminada en)\s*\d{4}\b", re.I)
_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_PHONE = re.compile(r"(?<![\w+])(?:(?:\+|00)\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}(?!\d)")
_MBREF = re.compile(r"\b(entidade|refer[eê]ncia|ref\.?)\s*:?\s*\d[\d\s]{4,12}\d", re.I)
_ORDER = re.compile(
    r"(?:#\s?\d{4,9}\b|"
    r"\b(?:pedido|encomenda|order|orden|commande|reserva|bestelling|bestellung|booking|reservation)"
    r"(?:\s*(?:n(?:[º°o]|umber|[úu]mero|um[ée]ro|ummer|r)?\.?|id|ref\.?))?\s*[:#]?\s?\d{4,9}\b|"
    r"\b(?:n[úu]mero|num[ée]ro|number|nummer|nr\.?|nº|n\.º)\s+(?:d[aeo]\s+|de\s+la\s+|of\s+|de\s+)?"
    r"(?:pedido|encomenda|commande|order|orden|reserva|bestelling|reservation)\s*[:#]?\s?\d{4,9}\b)",
    re.I)
_NIFKW = re.compile(r"\b(nif|nipc|contribuinte|vat|tva|n\.?\s?fiscal)\s*[:.]?\s*(?:PT)?\s?\d{9}\b", re.I)
_POST = re.compile(r"\b\d{4}-\d{3}\b")
_STREET = re.compile(r"\b(rua|avenida|av\.|travessa|largo|pra[cç]a|estrada|rue|calle|straat)\s+[^\n,]{3,60}", re.I)
_LONGNUM = re.compile(r"(?<![\d€$])\d(?:[\s.]?\d){5,}(?!\d)")
_BRPHONE = re.compile(r"(?<!\d)\(?\d{2}\)?\s?9?\s?\d{4}[\s-]\d{4}(?!\d)")
_TRACK = re.compile(r"\b[A-Z]{2}\d{9}[A-Z]{2}\b")
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_.]{2,30}")
_MONEY = re.compile(r"(€\s?\d|\d\s?(€|eur\b|euros?\b|r\$))", re.I)
_DATE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")

# --- positional names ---------------------------------------------------
_CAP = r"[A-ZÀ-Ý][a-zà-ÿ'’-]+"
_NAMESEQ = rf"{_CAP}(?:[ \t]+(?:d[aeo]s?|de|e|van|von|del|la)?[ \t]*{_CAP}){{0,3}}"
_GREET = re.compile(rf"\b((?i:ol[aá]|oi|bom dia|boa tarde|boa noite|hello|hi|dear|bonjour|bonsoir|hola|buenos d[ií]as|caro|cara|prezad[oa]|exm[oa]\.?|querid[oa])[,!]?[ \t]+)({_NAMESEQ})(?=[ \t]*(?:[,!.;:\n]|\Z))")
_SELF = re.compile(rf"\b((?i:o meu nome [eé]|meu nome [eé]|chamo-me|me chamo|daqui fala|fala-lhe|my name is|je m'appelle|je suis|me llamo|mi nombre es|mein name ist|ich hei[ßs]e|mijn naam is|em meu nome|em nome d[aeo]|a nome d[eo]|in the name of|au nom de|a nombre de|titular)[:,\s]+[\[\(\"“'«]?)((?:[a-zà-ÿ'’-]{{1,}}[ \t]+)?{_NAMESEQ})")
_CLOSE = re.compile(r"^\s*(obrigad[oa]s?|muito obrigad[oa]|cumprimentos|melhores cumprimentos|atenciosamente|atentamente|com os melhores cumprimentos|abra[cç]os?|um abra[cç]o|beijinhos|beijos|bjs|grat[oa]|gratid[ãa]o|sauda[cç][õo]es|agrade[cç]o|agradecid[oa]|muito agradecid[oa]|desde j[áa] agrade[cç]o|att|atte|cordialmente|best regards|kind regards|warm regards|regards|best|cheers|sincerely|sincerly|thanks|thank you|merci|cordialement|bien cordialement|cdlt|cdt|salutations|bien [àa] vous|saludos|un saludo|un abrazo|gracias|mit freundlichen gr[uü][sß]en|liebe gr[uü][sß]e|mfg|lg|groeten|met vriendelijke groet(?:en)?|vriendelijke groet(?:en)?|mvg)\b[^\n]{0,3}\n+\s*([^\n]{2,60})$", re.I | re.M)
_CLOSE_INLINE = re.compile(rf"\b((?i:obrigad[oa]|cumprimentos|atenciosamente|atentamente|grat[oa]|att\.?|atte\.?|regards|thanks|sincerely|merci|cordialement|cdlt|saludos|gracias|mvg|groeten)),?[ \t]+({_NAMESEQ})(?=[ \t]*(?:\[NAME\]|\[PHONE\]|$))", re.M)
# "Nome: X" / "Full name: X" / "Nom : X" fields written inline, not on their own line.
_INLINE_LABEL = re.compile(
    r"(?im)\b((?:nome completo|nome do comprador|nome do titular|nome|full name|name|nom complet|nom|"
    r"nombre completo|nombre|naam|titular|comprador|buyer|acheteur)"
    r"(?!\s+(?:d[oa]s?|de|del|of|de la)\s+(?:evento|artista|espet|show|event|concert|spectacle|[ée]v[ée]nement|empresa|company|soci[ée]t[ée]))"
    r"\s*(?:[:：]|\s-\s)\s*)([^\n]{2,80})")
_INLINE_STOP = re.compile(r"\s*(?:\[|\b(?:e-?mail|tel|telefone|telem[oó]vel|phone|contact|contacto|nif|n[úu]mero|numero|order|pedido|"
                          r"evento|event|data|date|morada|address|adresse)\b|\s[-–|]\s)", re.I)
# "o meu irmão Marco", "my wife Anna", "ma fille Léa".
_RELATIVE = re.compile(
    rf"\b((?i:(?:meu|minha|mon|ma|mi|my|mein|meine|mijn|nosso|nossa)\s+(?:irm[ãa]o|irm[ãa]|filh[oa]|marido|mulher|esposa|"
    rf"esposo|pai|m[ãa]e|amig[oa]|namorad[oa]|fr[èe]re|s[œo]eur|fils|fille|mari|femme|hermano|hermana|hijo|hija|brother|"
    rf"sister|son|daughter|husband|wife|friend|bruder|schwester|sohn|tochter|broer|zus|zoon|dochter)),?[ \t]+)({_NAMESEQ})")
# Courtesy titles, where surnames are often written in lowercase ("Mme colombelle").
# Dr/Dra are left out on purpose: here they almost always name a speaker, i.e. an event.
_TITLE = re.compile(r"\b((?i:mme|mlle|madame|monsieur|mr|mrs|ms|miss|sr|sra|srª|herr|frau|dhr|mevr)\.?[ \t]+)"
                    r"([A-Za-zÀ-ÿ'’-]{3,}(?:[ \t]+[A-ZÀ-Ý][a-zà-ÿ'’-]+)?)")
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_ODD_SPACE = re.compile("[   -   　]")
_FORM = re.compile(r"^(nome|name|e-?mail|telefone|telem[oó]vel|phone|contacto)\s*:?\s*\n+\s*([^\n]{2,80})$", re.I | re.M)
_SENTENCE_START = {"gostaria", "gostava", "estou", "venho", "preciso", "queria", "quero",
                   "fiz", "comprei", "recebi", "sou", "tenho", "efetuei", "pode", "podem",
                   "poderia", "poderiam", "espero", "escrevo", "solicito", "tentei",
                   "nous", "je", "we", "our", "please", "obrigado", "obrigada"}
_GREET_NOT = {"bom", "boa", "dia", "tarde", "noite", "equipa", "team", "senhores",
              "senhor", "senhora", "sr", "sra", "sir", "madam", "madame", "monsieur",
              "tudo", "todos", "all", "everyone", "lebillet", "suporte", "support"}
_PARTICLES = {"de", "da", "do", "dos", "das", "e", "van", "von", "del", "la"}
_LEAK_DIGITS = re.compile(r"(?<!\d)(?:\d[\s.-]?){8,}\d")
LINK_HOSTS = ("instagram.com", "facebook.com", "dropbox.com", "google.com",
              "youtube.com", "tiktok.com", "linkedin.com", "twitter.com", "x.com",
              "whatsapp.com", "wetransfer.com", "paypal.com", "apple.com", "aka.ms",
              "microsoft.com", "outlook.com", "bit.ly", "mbway.pt", "ctt.pt")
_HINTS = {"comprovativo": r"comprovativ|comprovante|receipt|recibo|proof|transfer",
          "fatura": r"fatur|factur|invoice|facture|rechnung",
          "bilhete": r"bilhete|ticket|ingresso|billet|entrada",
          "captura": r"screenshot|captura|screen|ecra|img_|whatsapp image",
          "documento_id": r"(^|[^a-z])(cc|bi)([^a-z]|$)|cartao|citizen|passaporte|passport|identidade"}

_SPLIT = re.compile(r"[^A-Za-zÀ-ÿ]+")


def name_tokens(display: str) -> set[str]:
    if "@" in (display or ""):
        return set()   # an address, not a name - local_tokens handles it
    return {t for t in _SPLIT.split(display or "")
            if len(t) >= 3 and fold(t) not in NOT_NAMES}


def local_tokens(addr: str) -> set[str]:
    local = addr.partition("@")[0]
    return {t for t in re.split(r"[._\-+\d]+", local)
            if len(t) >= 4 and t.isalpha() and fold(t) not in NOT_NAMES}


def person_like(display: str) -> bool:
    words = [w for w in _SPLIT.split(display or "") if w]
    return 1 <= len(words) <= 6 and not any(fold(w) in ORG_WORDS for w in words)


def compile_tokens(tokens: set[str]):
    if not tokens:
        return None
    alts = "|".join(accent_rx(t) for t in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"(?<![\w\[])(?:{alts})(?![\w\]])", re.I)


def compile_people(names: set[str]):
    """Full names of everyone who ever wrote in - redacted in every case."""
    if not names:
        return None
    alts = sorted({r"\s+".join(accent_rx(w) for w in n.split()) for n in names},
                  key=len, reverse=True)
    return re.compile(rf"(?<![\w\[])(?:{'|'.join(alts)})(?![\w\]])", re.I)


def compile_given(given: set[str]):
    """A known first name followed by capitalised words is a person."""
    if not given:
        return None
    alts = "|".join(accent_rx(g) for g in sorted(given, key=len, reverse=True))
    return re.compile(rf"(?<![\w\[])(?:(?=[A-ZÀ-Ý])(?i:{alts})[ \t]+{_CAP}"
                      rf"(?:[ \t]+(?:d[aeo]s?[ \t]+|de[ \t]+|e[ \t]+)?{_CAP}){{0,2}}(?![\w\]])"
                      # or any casing, directly before an already-redacted surname
                      rf"|(?i:{alts})(?=[ \t]+\[NAME\]))")


def looks_like_name(line: str) -> bool:
    words = line.split()
    if not 1 <= len(words) <= 5 or re.search(r"[.!?:;\[\d@]", line):
        return False
    return all(w[:1].isupper() for w in words if w.lower() not in _PARTICLES)


# Staff reply from atendimento@, so they never appear as senders in this
# inbox - only in quoted attributions such as
# "Mensagem de Fabia Antunes <atendimento@lebillet.eu>".
_STAFF_ATTRIB = re.compile(
    r"([A-ZÀ-Ý][a-zà-ÿ]+(?:[ \t]+[A-ZÀ-Ý][a-zà-ÿ]+){0,3})[ \t]*"
    r"<[^<>@\s]+@lebillet\.(?:eu|com\.br|pt)>")


def staff_from_attributions(bodies) -> set[str]:
    return {n for b in bodies for n in _STAFF_ATTRIB.findall(b or "") if person_like(n)}


def build_keep_list(cases: list[dict], sender_full: set[str],
                    staff_tokens=frozenset()) -> list[str]:
    """Capitalised phrases used by many different customers are public names
    (artists, events, venues). A phrase that is also someone's full display
    name is a person, however often it appears, so it is not kept."""
    # Same line only: across a line break, phrases glue into "Bilhetes⏎Estão".
    seq = re.compile(rf"{_CAP}(?:[ \t]+(?:d[aeo]s?[ \t]+|de[ \t]+|e[ \t]+)?{_CAP}){{1,3}}")
    seen: dict[str, set[str]] = defaultdict(set)
    for c in cases:
        who = (c["customer_addresses"] or [c["case_id"]])[0]
        for p in set(seq.findall(c["subject"] + "\n" + c["customer_text"])):
            seen[p].add(who)
    staff_f = {fold(t) for t in staff_tokens}
    ignore = NOT_NAMES | _PARTICLES | _GREET_NOT

    def staff_only(p: str) -> bool:
        # "Olá Fabia" recurs across customers but is a greeting to staff.
        content = [w for w in fold(p).split() if w not in ignore]
        return bool(content) and all(w in staff_f for w in content)

    return sorted((p for p, w in seen.items()
                   if len(w) >= KEEP_MIN_CUSTOMERS and fold(p) not in sender_full
                   and not staff_only(p)),
                  key=len, reverse=True)


def attachment_hints(msgs: list[dict]) -> list[str]:
    hints: set[str] = set()
    for m in msgs:
        for a in m.get("attachments", []):
            fn = fold(a.get("filename") or "")
            ct = a.get("content_type", "")
            if re.match(r"image\d{3}\.", fn) or a.get("approx_bytes", 0) < 8000:
                continue  # signature logos, tracking pixels
            if ct.startswith("image/"):
                hints.add("image")
            elif ct == "application/pdf":
                hints.add("pdf")
            hints.update(k for k, p in _HINTS.items() if re.search(p, fn))
    return sorted(hints)


def redact(text: str, name_rx, keep_rx, counts: Counter, people_rx=None, given_rx=None,
           found: list | None = None) -> str:
    """Redact one text. Every positional name match is appended to `found`,
    so propagate_names() can remove its other mentions in the same case."""
    if not text:
        return ""
    if found is None:
        found = []
    # Zero-width and non-breaking spaces break "[NAME] Surname" matching.
    text = _ODD_SPACE.sub(" ", _ZERO_WIDTH.sub("", text))
    kept: list[str] = []

    def protect(m):
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    def put(tag):
        def sub(m):
            counts[tag] += 1
            return f"[{tag}]"
        return sub

    def keep_label(tag, group=1):
        def sub(m):
            counts[tag] += 1
            return f"{m.group(group)} [{tag}]"
        return sub

    def url(m):
        u = m.group(0).rstrip(".,;:")
        host = re.sub(r"^(https?://)?(www\.)?", "", u, flags=re.I).split("/")[0].split("?")[0].lower()
        counts["LINK"] += 1
        if host.endswith(INTERNAL):
            low = u.lower()
            return "[ORDER_LINK]" if "/order" in low else "[EVENT_LINK]" if "/event" in low else "[LEBILLET_LINK]"
        known = next((h for h in LINK_HOSTS if host == h or host.endswith("." + h)), None)
        return f"[LINK:{known}]" if known else "[LINK]"

    def card(m):
        if not luhn_ok(m.group(0)):
            return m.group(0)
        counts["CARD"] += 1
        return "[CARD]"

    def phone(m):
        s = m.group(0)
        d = re.sub(r"\D", "", s)
        if not 9 <= len(d) <= 15 or _DATE.match(s.strip()):
            return s
        counts["PHONE"] += 1
        return "[PHONE]"

    def longnum(m):
        if _DATE.match(m.group(0).strip()):
            return m.group(0)
        counts["NUM"] += 1
        return "[NUM]"

    def order(m):
        counts["ORDER_REF"] += 1
        if m.group(0).lstrip().startswith("#"):
            return "[ORDER_REF]"
        return re.sub(r"[\s:#]*\d{4,9}$", "", m.group(0)) + " [ORDER_REF]"   # keep "Order number"

    def street(m):
        if not re.search(r"\d", m.group(0)):
            return m.group(0)
        counts["ADDRESS"] += 1
        return "[ADDRESS]"

    def form(m):
        label = m.group(1).lower()
        tag = "EMAIL" if "mail" in label else "PHONE" if re.search(r"tel|phone|contact", label) else "NAME"
        if tag == "NAME":
            found.append(m.group(2))
        counts[tag] += 1
        return f"{m.group(1)}\n[{tag}]"

    def greet(m):
        if fold(m.group(2).split()[0]) in _GREET_NOT | NOT_NAMES | _SENTENCE_START:
            return m.group(0)
        found.append(m.group(2))
        counts["NAME"] += 1
        return f"{m.group(1)}[NAME]"

    def named(m):
        found.append(m.group(2))
        counts["NAME"] += 1
        return f"{m.group(1)}[NAME]"

    def whole(m):
        found.append(m.group(0))
        counts["NAME"] += 1
        return "[NAME]"

    def inline(m):
        value = m.group(2)
        stop = _INLINE_STOP.search(value)
        name = (value[:stop.start()] if stop else value).strip(" ,;.")
        if not re.search(r"[A-Za-zÀ-ÿ]{2,}", name) or len(name) > 70:
            return m.group(0)
        found.append(name)
        counts["NAME"] += 1
        return m.group(1) + "[NAME]" + (value[stop.start():] if stop else "")

    def close(m):
        if not looks_like_name(m.group(2).strip()):
            return m.group(0)
        found.append(m.group(2).strip())
        counts["NAME"] += 1
        return m.group(0)[: m.start(2) - m.start(0)] + "[NAME]"

    if keep_rx:
        text = keep_rx.sub(protect, text)
    text = _URL.sub(url, text)
    text = _EMAIL.sub(put("EMAIL"), text)
    text = _EMAIL_OBF.sub(put("EMAIL"), text)
    text = _HANDLE.sub(put("HANDLE"), text)
    text = _IBAN.sub(put("IBAN"), text)
    text = _CPF.sub(put("CPF"), text)
    text = _TRACK.sub(put("TRACKING"), text)
    text = _CARD.sub(card, text)
    text = _CARD4.sub(keep_label("CARD_LAST4"), text)
    text = _MBREF.sub(keep_label("MB_REF"), text)
    text = _NIFKW.sub(keep_label("NIF"), text)
    text = _ORDER.sub(order, text)
    text = _BRPHONE.sub(put("PHONE"), text)
    text = _PHONE.sub(phone, text)
    text = re.sub(r"\+\d{1,3}[ \t]*\[PHONE\]", "[PHONE]", text)
    text = _POST.sub(put("POSTCODE"), text)
    text = _STREET.sub(street, text)
    text = _LONGNUM.sub(longnum, text)
    text = _FORM.sub(form, text)
    text = _INLINE_LABEL.sub(inline, text)
    if people_rx:
        text = people_rx.sub(whole, text)
    if given_rx:
        text = given_rx.sub(whole, text)
    text = _GREET.sub(greet, text)
    text = _SELF.sub(named, text)
    text = _RELATIVE.sub(named, text)
    text = _TITLE.sub(named, text)
    text = _CLOSE.sub(close, text)
    text = _CLOSE_INLINE.sub(named, text)
    if name_rx:
        text = name_rx.sub(put("NAME"), text)
    if given_rx:   # second pass: "MANUELA [NAME]" once the surname is gone
        text = given_rx.sub(whole, text)
    # A surname left beside a redacted first name ("[NAME] de Souza").
    text = re.sub(rf"\[NAME\](?:[ \t]+(?:d[aeo]s?[ \t]+|de[ \t]+|e[ \t]+)?{_CAP})+", "[NAME]", text)
    # A first name alone on the line after a redacted surname, followed by an
    # address or number ("[NAME]⏎Christophe 5 [ADDRESS]").
    text = re.sub(rf"(\[NAME\][ \t]*\n[ \t]*){_CAP}(?=[ \t]*(?:\d|\[|$))", r"\1[NAME]", text, flags=re.M)
    # ALL-CAPS name parts beside a redacted name ("SALDANHA [NAME]").
    text = re.sub(r"(?:\b[A-ZÀ-Ý]{3,}[ \t]+){1,2}\[NAME\]|\[NAME\](?:[ \t]+[A-ZÀ-Ý]{3,}\b){1,2}",
                  "[NAME]", text)
    text = re.sub(r"\[NAME\](?:[\s,]+\[NAME\])+", "[NAME]", text)
    return re.sub("\x00(\\d+)\x00", lambda m: kept[int(m.group(1))], text)


def propagate_names(text: str, found: list, keep_rx, counts: Counter) -> str:
    """Remove other mentions of names already found in the same case:
    "o meu irmão Marco ... o Marco não recebeu"."""
    skip = NOT_NAMES | _PARTICLES | _GREET_NOT | _SENTENCE_START | TITLES
    words = {w for f in found for w in re.findall(r"[A-Za-zÀ-ÿ'’-]{3,}", f) if fold(w) not in skip}
    if not text or not words:
        return text
    kept: list[str] = []
    if keep_rx:
        text = keep_rx.sub(lambda m: kept.append(m.group(0)) or f"\x00{len(kept) - 1}\x00", text)
    # Case-sensitive on purpose: "Rosa" is a name, "rosa" is a colour.
    forms = sorted({v for w in words for v in (w, w.capitalize(), w.upper())}, key=len, reverse=True)
    rx = re.compile(r"(?<![\w\[])(?:" + "|".join(map(re.escape, forms)) + r")(?![\w\]])")
    text, n = rx.subn("[NAME]", text)
    counts["NAME"] += n
    text = re.sub(r"\[NAME\](?:[\s,]+\[NAME\])+", "[NAME]", text)
    return re.sub("\x00(\\d+)\x00", lambda m: kept[int(m.group(1))], text)


def leak_scan(text: str, tokens: set[str], keep_rx) -> list[str]:
    """Independent second pass. Anything flagged here blocks the case."""
    t = keep_rx.sub(" ", text) if keep_rx else text
    t = re.sub(r"\[[^\]]*\]", " ", t)
    leaks = []
    if re.search(r"\w@|@\w", t):   # "Show @ Venue" is not an address
        leaks.append("at_sign")
    if re.search(r"https?://|www\.", t, re.I):
        leaks.append("url")
    if _LEAK_DIGITS.search(t):
        leaks.append("long_number")
    ft = fold(t)
    if any(re.search(rf"\b{re.escape(fold(tok))}\b", ft) for tok in tokens):
        leaks.append("header_name")
    return leaks
