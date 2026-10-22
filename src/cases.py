#!/usr/bin/env python3
"""Stage 3 - group triaged messages into support cases.

A case is one customer's problem, however many messages it took. Messages
are linked by RFC5322 References/In-Reply-To first, which is reliable, and
only then by normalised subject *plus the same customer address*, which is
not. Subject alone is never used: ten customers asking "Bilhetes" are ten
cases, not one.

Excluded buckets are written to a separate quarantine file so they can be
reviewed before anyone deletes them.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
IN = DATA / "02_triaged.jsonl"
OUT = DATA / "03_cases.jsonl"
QUARANTINE = DATA / "03_quarantine.jsonl"

ANALYSE = {"customer", "staff_reply"}
INTERNAL_DOMAINS = {"lebillet.eu", "lebillet.com.br", "lebillet.pt", "tickett.com", "mytickett.com"}
SUBJECT_WINDOW = timedelta(days=60)

_PREFIX_RE = re.compile(
    r"^\s*(re|res|rif|fw|fwd|enc|tr|rv|antw|aw|vs)\s*(\[\d+\])?\s*:\s*",
    re.IGNORECASE,
)
_HDR_WORDS = (r"From|De|Van|Von|Sent|Enviado|Enviada|Envoy[ée]|Date|Data|Datum|"
              r"Fecha|To|Para|Aan|An|[ÀA]|Cc|Subject|Assunto|Asunto|Objet|"
              r"Onderwerp|Betreff")
# A forward marker: the customer's real request comes AFTER this line.
_FWD_MARK_RE = re.compile(
    r"^\s*(-{2,}\s*(Forwarded message|Mensagem reencaminhada|Mensagem encaminhada|"
    r"Message transf[ée]r[ée]|Weitergeleitete Nachricht|Mensaje reenviado|"
    r"Original Message|Mensagem original|Message d'origine)\s*-{2,}|"
    r"Begin forwarded message:|In[ií]cio da mensagem (re)?encaminhada:)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_FWD_SUBJ_RE = re.compile(r"^\s*(fw|fwd|enc|tr|rv)\s*:", re.IGNORECASE)
_HDR_LINE_RE = re.compile(rf"^\s*\*?({_HDR_WORDS})\s*:\*?.*$", re.IGNORECASE | re.MULTILINE)
# A reply attribution: what follows is someone else's older message. Gmail
# wraps long attributions, so allow one line break inside.
_ATTRIB_RE = re.compile(
    r"^\s*(On|Em|El|Le|Am|Op)\b[^\n]{3,160}?(\n[^\n]{0,100}?)?"
    r"\b(wrote|escreveu|escribi[óo]|a écrit|[ée]crit|schrieb|schreef)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
# Outlook quotes have no attribution line, just a From/De header block.
_OUTLOOK_RE = re.compile(
    rf"^\s*\*?(From|De|Van|Von)\s*:\*?[^\n]{{3,160}}\n\s*\*?({_HDR_WORDS})\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_SIG_RE = re.compile(r"^\s*(--\s*$|__+$)", re.MULTILINE)
_DEVICE_RE = re.compile(r"^\s*(Enviado do meu|Enviado a partir d[oa]|Enviado desde|Sent from my|Gesendet von|Verstuurd vanaf|Envoy[ée] depuis|Envoy[ée] de mon|"
                        r"Obtenha o Outlook|Get Outlook|Von meinem).*$",
                        re.IGNORECASE | re.MULTILINE)


def normalise_subject(subject: str) -> str:
    prev = None
    while prev != subject:
        prev, subject = subject, _PREFIX_RE.sub("", subject)
    return re.sub(r"\s+", " ", subject).strip().lower()


def is_forward(subject: str, body: str) -> bool:
    return bool(_FWD_SUBJ_RE.match(subject or "") or _FWD_MARK_RE.search(body or ""))


def _drop_quote_lines(text: str) -> str:
    return "\n".join(l for l in text.split("\n") if not l.lstrip().startswith(">"))


def strip_quotes(body: str, forwarded: bool = False) -> str:
    """Keep only what the customer actually wrote.

    Replies: cut at the first attribution or Outlook header block, and drop
    inline '>' lines. Forwards are the opposite case - a partner forwarding
    a customer puts the customer's words *below* the marker - so nothing is
    cut; markers, header lines and quote prefixes are removed instead.
    """
    if forwarded:
        text = _FWD_MARK_RE.sub("", body)
        text = _ATTRIB_RE.sub("", text)
        text = _HDR_LINE_RE.sub("", text)
        text = "\n".join(re.sub(r"^\s*(>\s?)+", "", l) for l in text.split("\n"))
    else:
        cuts = [m.start() for rx in (_ATTRIB_RE, _OUTLOOK_RE, _FWD_MARK_RE)
                if (m := rx.search(body))]
        text = body[:min(cuts)] if cuts else body
        text = _drop_quote_lines(text)
        m = _SIG_RE.search(text)
        if m:
            text = text[:m.start()]
    text = _DEVICE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def customer_address(rec: dict) -> str:
    """The customer in this exchange, whichever direction it went."""
    if rec["bucket"] == "staff_reply":
        for addr in rec.get("to", []):
            if addr.rpartition("@")[2] not in INTERNAL_DOMAINS:
                return addr
        return ""
    return rec.get("from_email", "")


class Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def stable_case_id(first: dict) -> str:
    """Derived from the case's earliest message, so it survives re-runs.

    A positional counter would renumber every case whenever new mail
    arrives, silently detaching hand labels and cached results.
    """
    key = first.get("message_id") or first["id"]
    return "C" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def parse_date(rec: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(rec["date"]) if rec.get("date") else None
    except ValueError:
        return None


def build_cases(records: list[dict]) -> list[dict]:
    uf = Union()
    by_msgid: dict[str, str] = {}
    for rec in records:
        uf.find(rec["id"])
        if rec.get("message_id"):
            by_msgid[rec["message_id"]] = rec["id"]

    linked_by_refs: set[str] = set()
    for rec in records:
        refs = list(rec.get("references") or [])
        if rec.get("in_reply_to"):
            refs.append(rec["in_reply_to"])
        for ref in refs:
            target = by_msgid.get(ref)
            if target and target != rec["id"]:
                uf.union(rec["id"], target)
                linked_by_refs.add(rec["id"])
                linked_by_refs.add(target)

    # Fallback: same normalised subject AND same customer, close in time.
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        subject = normalise_subject(rec.get("subject", ""))
        addr = customer_address(rec)
        if subject and addr:
            buckets[(subject, addr)].append(rec)

    linked_by_subject: set[str] = set()
    for group in buckets.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: parse_date(r) or datetime.min.replace(tzinfo=None))
        for earlier, later in zip(group, group[1:]):
            d1, d2 = parse_date(earlier), parse_date(later)
            if d1 and d2 and abs(d2 - d1) > SUBJECT_WINDOW:
                continue
            if uf.find(earlier["id"]) != uf.find(later["id"]):
                uf.union(earlier["id"], later["id"])
                linked_by_subject.update({earlier["id"], later["id"]})

    grouped: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        grouped[uf.find(rec["id"])].append(rec)

    cases = []
    for msgs in grouped.values():
        msgs.sort(key=lambda r: (parse_date(r) is None, parse_date(r) or datetime.min, r["id"]))
        from_customer = [m for m in msgs if m["bucket"] == "customer"]
        from_staff = [m for m in msgs if m["bucket"] == "staff_reply"]

        customer_text = "\n\n".join(
            t for m in from_customer
            if (t := strip_quotes(m.get("body_text", ""),
                                  is_forward(m.get("subject", ""), m.get("body_text", ""))))
        )
        # Subject-only messages are common; keep the subject as content.
        subjects = [m.get("subject", "") for m in msgs if m.get("subject")]
        addrs = {a for m in msgs if (a := customer_address(m))}
        dates = [d for m in msgs if (d := parse_date(m))]
        langs = [m.get("lang_guess") for m in from_customer or msgs]

        if len(msgs) == 1:
            method = "single"
        elif any(m["id"] in linked_by_refs for m in msgs):
            method = "references"
        else:
            method = "subject"

        cases.append({
            "case_id": stable_case_id(msgs[0]),
            "message_ids": [m["id"] for m in msgs],
            "n_messages": len(msgs),
            "n_customer": len(from_customer),
            "n_staff_replies": len(from_staff),
            "link_method": method,
            "customer_addresses": sorted(addrs),
            "customer_domain": (sorted(addrs)[0].rpartition("@")[2] if addrs else ""),
            "subject": subjects[0] if subjects else "",
            "first_date": min(dates).isoformat() if dates else "",
            "last_date": max(dates).isoformat() if dates else "",
            "languages": sorted({l for l in langs if l and l not in ("unknown", "unclear")}),
            "attachment_count": sum(m.get("attachment_count", 0) for m in msgs),
            "customer_text": customer_text[:12000],
            "customer_chars": len(customer_text),
            "has_staff_reply": bool(from_staff),
        })
    cases.sort(key=lambda c: (c["first_date"], c["case_id"]))
    return cases


def main() -> int:
    if not IN.exists():
        print(f"missing {IN} - run triage.py first", file=sys.stderr)
        return 1

    records, excluded = [], []
    with IN.open(encoding="utf-8") as src:
        for line in src:
            rec = json.loads(line)
            (records if rec["bucket"] in ANALYSE else excluded).append(rec)

    cases = build_cases(records)

    with OUT.open("w", encoding="utf-8") as dst:
        for case in cases:
            dst.write(json.dumps(case, ensure_ascii=False) + "\n")
    with QUARANTINE.open("w", encoding="utf-8") as dst:
        for rec in excluded:
            dst.write(json.dumps(rec, ensure_ascii=False) + "\n")

    multi = [c for c in cases if c["n_messages"] > 1]
    methods: dict[str, int] = {}
    for case in cases:
        methods[case["link_method"]] = methods.get(case["link_method"], 0) + 1

    print(f"{len(records)} messages -> {len(cases)} cases  ({OUT})", file=sys.stderr)
    print(f"quarantined {len(excluded)} messages       ({QUARANTINE})\n", file=sys.stderr)
    print(f"  multi-message cases : {len(multi)}", file=sys.stderr)
    print(f"  with a staff reply  : {sum(1 for c in cases if c['has_staff_reply'])}", file=sys.stderr)
    print(f"  empty customer text : {sum(1 for c in cases if not c['customer_chars'])}", file=sys.stderr)
    for method, n in sorted(methods.items(), key=lambda kv: -kv[1]):
        print(f"  linked by {method:<11}: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
