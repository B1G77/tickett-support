#!/usr/bin/env python3
"""Stage 5 - carve out the held-out test set, then the discovery sample.

Order matters. The test set is drawn FIRST and never shown to the model
during discovery or prompt writing, otherwise its accuracy numbers mean
nothing. Both draws are seeded, so re-running gives the same cases.

- Test set: representative. Stratified by month so every period is
  present, otherwise uniform. This is what quality is measured on.
- Discovery sample: deliberately NOT representative. Over-weights recent
  months (future mail looks like recent mail), minority languages, cases no
  keyword recognised, and multi-theme cases, and caps any single event so
  one cancelled show cannot become a category of its own. Split into three
  disjoint batches so the taxonomy can be proposed three times and only
  the categories that recur are trusted.

Only cases that passed the leak scan (send_ok) are eligible for either.
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
REDACTED = DATA / "04_redacted.jsonl"
CASES = DATA / "03_cases.jsonl"
TEST_OUT = DATA / "05_test_set.jsonl"
DISC_OUT = DATA / "05_discovery_sample.jsonl"

SEED = 20260910
TEST_SIZE = 300
DISCOVERY_SIZE = 360
BATCHES = ("A", "B", "C")
EVENT_CAP = 0.05
RECENT_FROM = "2026-03"

# Same crude themes as the preview. Used only to spread the sample - never
# as labels.
THEMES = {
    "reembolso": r"reembols|devolu[cç][aã]o|refund|rembours|estorno",
    "tecnico": r"n[aã]o consigo|erro|error|site|login|password|app\b",
    "info": r"que horas|hor[aá]rio|abertura|idade|crian[cç]a|estacionamento|acesso|lugar|mesa",
    "pagamento": r"pagamento|cobran[cç]a|cobrad|payment|paiement|cart[aã]o|mb ?way|multibanco",
    "cancelamento": r"cancel|adiad|remarcad|postpon|annul",
    "nao_recebido": r"n[aã]o (recebi|chegou)|didn.t receive|not received",
    "alteracao": r"troca|trocar|alterar|altera[cç][aã]o|mudar|transferir",
    "fatura": r"fatura|factura|recibo|\bnif\b|invoice",
}
RARE = {"nao_recebido", "alteracao", "fatura"}
# Template text that carries no request: the Calema contact form sends only
# name/e-mail/phone. Such cases get a fixed route, not an AI category, so they
# are kept out of discovery (they stay in the test set, flagged).
_TEMPLATE = re.compile(
    r"\[[^\]]*\]|you have a new website form submission|esta mensagem foi enviada de|"
    r"nova entrada de formul[áa]rio(\s*\[[^\]]*\])?(\s+para contato)?|\b(nome|e-?mail|telefone)\b", re.I)


def content_free(case: dict) -> bool:
    rest = _TEMPLATE.sub(" ", case["subject"] + " " + case["text"])
    return len(re.findall(r"[A-Za-zÀ-ÿ]{2,}", rest)) < 5


def themes_of(text: str) -> list[str]:
    low = text.lower()
    return [k for k, p in THEMES.items() if re.search(p, low)]


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def weight(case: dict) -> float:
    w = 2.0 if case["first_month"] >= RECENT_FROM else 1.0
    langs = set(case["languages"])
    if langs & {"fr", "es", "de", "nl"}:
        w *= 2.5
    elif "en" in langs:
        w *= 1.5
    th = case["_themes"]
    if not th:
        w *= 2.0
    elif len(th) > 1:
        w *= 1.5
    if RARE & set(th):
        w *= 1.5
    if case["n_staff_replies"]:
        w *= 1.3
    return w


def weighted_draw(pool: list[dict], n: int, rng: random.Random, cap: int) -> list[dict]:
    """Efraimidis-Spirakis weighted sampling without replacement, with a
    per-event cap applied in key order."""
    keyed = sorted(pool, key=lambda c: rng.random() ** (1.0 / weight(c)), reverse=True)
    picked, per_event = [], Counter()
    for c in keyed:
        ev = c["_event"]
        if ev and per_event[ev] >= cap:
            continue
        picked.append(c)
        per_event[ev] += 1
        if len(picked) == n:
            break
    return picked


def main() -> int:
    red = load(REDACTED)
    raw = {c["case_id"]: c for c in load(CASES)}
    keep_counts = Counter()
    for r in red:
        # The event a case is about: its most frequent protected public name.
        text = raw[r["case_id"]]["subject"] + "\n" + raw[r["case_id"]]["customer_text"]
        r["_themes"] = themes_of(text)
        r["_event"] = ""
    eligible = [r for r in red if r["send_ok"] and (r["text"] or r["subject"])]
    for r in eligible:
        r["content_free"] = content_free(r)

    # Event key: capitalised public names protected by the redactor appear
    # verbatim in the redacted text; take the most common 2-4 word run.
    seq = re.compile(r"[A-ZÀ-Ý][a-zà-ÿ]+(?:[ \t]+(?:d[aeo]s?[ \t]+|de[ \t]+)?[A-ZÀ-Ý][a-zà-ÿ]+){1,3}")
    for r in eligible:
        keep_counts.update(set(seq.findall(r["subject"] + " " + r["text"])))
    for r in eligible:
        runs = [p for p in seq.findall(r["subject"] + " " + r["text"]) if keep_counts[p] >= 5]
        r["_event"] = max(runs, key=lambda p: keep_counts[p]) if runs else ""

    rng = random.Random(SEED)

    by_month = defaultdict(list)
    for r in eligible:
        by_month[r["first_month"]].append(r)
    test = []
    for month, rows in sorted(by_month.items()):
        k = round(TEST_SIZE * len(rows) / len(eligible))
        test += rng.sample(rows, min(k, len(rows)))
    test_ids = {r["case_id"] for r in test}

    pool = [r for r in eligible if r["case_id"] not in test_ids and not r["content_free"]]
    disc = weighted_draw(pool, DISCOVERY_SIZE, rng, max(1, int(EVENT_CAP * DISCOVERY_SIZE)))
    rng.shuffle(disc)
    for i, r in enumerate(disc):
        r["batch"] = BATCHES[i % len(BATCHES)]

    def strip(r):
        return {k: v for k, v in r.items() if not k.startswith("_")}

    with TEST_OUT.open("w", encoding="utf-8") as f:
        for r in sorted(test, key=lambda r: r["case_id"]):
            f.write(json.dumps(strip(r), ensure_ascii=False) + "\n")
    with DISC_OUT.open("w", encoding="utf-8") as f:
        for r in disc:
            f.write(json.dumps(strip(r), ensure_ascii=False) + "\n")

    def dist(rows, key):
        c = Counter(v for r in rows for v in key(r))
        return {k: f"{100 * n / len(rows):.0f}%" for k, n in c.most_common()}

    print(f"eligible {len(eligible)} of {len(red)} cases "
          f"(excluded: {len(red) - len(eligible)} blocked or empty)", file=sys.stderr)
    print(f"test set  {len(test)} -> {TEST_OUT.name}  "
          f"(content-free, flagged: {sum(r['content_free'] for r in test)})", file=sys.stderr)
    print(f"content-free cases kept out of discovery: {sum(r['content_free'] for r in eligible)}", file=sys.stderr)
    print(f"discovery {len(disc)} -> {DISC_OUT.name}  batches "
          f"{dict(Counter(r['batch'] for r in disc))}", file=sys.stderr)
    for label, key in (("recent (>= " + RECENT_FROM + ")", lambda r: ["yes" if r["first_month"] >= RECENT_FROM else "no"]),
                       ("language", lambda r: r["languages"] or ["?"]),
                       ("theme", lambda r: r["_themes"] or ["(none)"])):
        print(f"\n{label}", file=sys.stderr)
        for name, rows in (("population", eligible), ("test", test), ("discovery", disc)):
            print(f"  {name:<10} {dist(rows, key)}", file=sys.stderr)
    top_event = Counter(r["_event"] for r in disc if r["_event"]).most_common(3)
    print(f"\nlargest events in discovery (cap {int(EVENT_CAP * DISCOVERY_SIZE)}): {top_event}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
