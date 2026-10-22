#!/usr/bin/env python3
"""Stage 4 runner, and the redactor the router reuses for new mail.

src/redact.py holds the rules. This module builds the archive-wide context
once - staff names, public (artist/event/venue) names, known correspondents,
common first names - and applies it to a whole case or to a single email,
so new mail is redacted exactly like the archive was.

Output is built from an explicit allowlist. Raw subject, addresses and
attachment filenames never reach it.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import redact as R

DATA = Path(__file__).resolve().parent.parent / "data"
CASES = DATA / "03_cases.jsonl"
MESSAGES = DATA / "02_triaged.jsonl"
OUT = DATA / "04_redacted.jsonl"


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


class Redactor:
    def __init__(self, messages: list[dict], cases: list[dict]):
        self.staff: set[str] = set()
        staff_full: set[str] = set()
        for m in messages:
            if m.get("from_domain", "").endswith(R.INTERNAL) and R.person_like(m.get("from_name", "")):
                self.staff |= R.name_tokens(m["from_name"])
                staff_full.add(R.fold(m["from_name"]).strip())
        for n in R.staff_from_attributions(m.get("body_text", "") for m in messages):
            self.staff |= R.name_tokens(n)
            staff_full.add(R.fold(n))
        # Staff are people however often they are mentioned; anyone else named
        # by 5+ different customers is an artist, event or venue.
        self.keep = R.build_keep_list(cases, staff_full, self.staff)
        keep_f = {R.fold(k) for k in self.keep}
        self.keep_rx = (re.compile(r"\b(?:" + "|".join(map(re.escape, self.keep)) + r")\b")
                        if self.keep else None)
        people, given = set(), Counter()
        for m in messages:
            n = (m.get("from_name") or "").strip(" \"'")
            words = [w for w in R._SPLIT.split(n) if w]
            if (m.get("bucket") in ("customer", "staff_reply", "internal") and R.person_like(n)
                    and len(words) >= 2 and R.fold(" ".join(words)) not in keep_f):
                people.add(" ".join(words))
                first = R.fold(words[0])
                if len(first) >= 3 and first not in R.NOT_NAMES | R.TITLES | R.ORG_WORDS:
                    given[first] += 1
        # A single alternation of ~2,000 names is far too slow, so each text
        # only compiles the names that could possibly occur in it.
        self.people = [(n, R.fold(n.split()[0]), R.fold(n.split()[-1])) for n in people]
        self.given = {g for g, c in given.items() if c >= 2}

    def tokens_for(self, from_names, addresses) -> set[str]:
        tokens = set(self.staff)
        for n in from_names:
            if R.person_like(n):
                tokens |= R.name_tokens(n)
        for a in addresses:
            tokens |= R.local_tokens(a)
        return tokens

    def redact(self, subject: str, text: str, from_names=(), addresses=()):
        """Return (subject, text, placeholder counts, leak flags)."""
        tokens = self.tokens_for(from_names, addresses)
        folded = R.fold(subject + "\n" + text)
        people_rx = R.compile_people({n for n, a, b in self.people if a in folded and b in folded})
        given_rx = R.compile_given({g for g in self.given if g in folded})
        name_rx = R.compile_tokens(tokens)
        counts: Counter = Counter()
        found: list[str] = []
        s = R.redact(subject, name_rx, self.keep_rx, counts, people_rx, given_rx, found)
        t = R.redact(text, name_rx, self.keep_rx, counts, people_rx, given_rx, found)
        s = R.propagate_names(s, found, self.keep_rx, counts)
        t = R.propagate_names(t, found, self.keep_rx, counts)
        return s, t, counts, R.leak_scan(s + "\n" + t, tokens, self.keep_rx)


def redact_case(red: "Redactor", c: dict, by_id: dict) -> tuple[dict, Counter, list]:
    cm = [by_id[i] for i in c["message_ids"] if i in by_id]
    names = [m.get("from_name", "") for m in cm if m["bucket"] == "customer"]
    subject, text, counts, leaks = red.redact(c["subject"], c["customer_text"],
                                              names, c["customer_addresses"])
    return {   # explicit allowlist - nothing else leaves
        "case_id": c["case_id"],
        "redactor": R.VERSION,
        "subject": subject,
        "text": text,
        "languages": c["languages"],
        "first_month": c["first_date"][:7],
        "n_messages": c["n_messages"],
        "n_customer": c["n_customer"],
        "n_staff_replies": c["n_staff_replies"],
        "attachment_count": c["attachment_count"],
        "attachment_hints": R.attachment_hints(cm),
        "placeholders": dict(counts),
        "leak_flags": leaks,
        "send_ok": not leaks,
    }, counts, leaks


def main() -> int:
    for need in (CASES, MESSAGES):
        if not need.exists():
            print(f"missing {need}", file=sys.stderr)
            return 1
    msgs = load(MESSAGES)
    cases = load(CASES)
    by_id = {m["id"]: m for m in msgs}
    red = Redactor(msgs, cases)

    totals: Counter = Counter()
    flags: Counter = Counter()
    blocked = 0
    with OUT.open("w", encoding="utf-8") as out:
        for c in cases:
            row, counts, leaks = redact_case(red, c, by_id)
            totals.update(counts)
            flags.update(leaks)
            blocked += bool(leaks)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"redacted {len(cases)} cases -> {OUT}  ({R.VERSION})", file=sys.stderr)
    print(f"  public names kept: {len(red.keep)} | known people: {len(red.people)} | first names: {len(red.given)}",
          file=sys.stderr)
    print(f"  placeholders: {dict(totals.most_common())}", file=sys.stderr)
    print(f"  BLOCKED by leak scan: {blocked}  {dict(flags)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
