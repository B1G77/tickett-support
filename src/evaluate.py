#!/usr/bin/env python3
"""Measure the classifier against hand labels before trusting it with refunds.

Two label sets, reported separately and never pooled:
  discovery  360 cases labelled in-session while the folders were defined.
             Optimistic by construction: the definitions were written from them.
  test       the user's blind labels (data/test_labels.csv from the labelling
             page). This is the honest number.

The current redaction (data/04_redacted.jsonl) is what gets sent, exactly as
the router would send it. Predictions are cached per case, redactor version
and model, so re-running a report costs nothing.

Usage: evaluate.py [--set discovery|test|both]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import router as RT

DATA = Path(__file__).resolve().parent.parent / "data"
LABELS = DATA / "06_labels.json"
TEST_CSV = DATA / "test_labels.csv"
REDACTED = DATA / "04_redacted.jsonl"
MONEY = "F1"   # the folder where a miss delays somebody's refund


def gold_sets() -> dict[str, dict[str, dict]]:
    sets = {}
    if LABELS.exists():
        raw = json.loads(LABELS.read_text())
        sets["discovery"] = {cid: {"folder": v["v2"], "urgent": v["urgent"]}
                             for batch in raw.values() for cid, v in batch.items()}
    if TEST_CSV.exists():
        with TEST_CSV.open(encoding="utf-8-sig") as f:
            sets["test"] = {r["case_id"]: {"folder": r["folder_id"], "urgent": r["urgent"] == "1"}
                            for r in csv.DictReader(f)}
    return sets


def predict(cfg: dict, case_ids: set[str]) -> dict[str, dict]:
    cache_path = cfg["state_dir"] / "eval_predictions.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    redacted = {r["case_id"]: r for r in map(json.loads, REDACTED.open(encoding="utf-8"))}
    model = cfg["model"]
    cache = {}
    if cache_path.exists():
        for r in map(json.loads, cache_path.open(encoding="utf-8")):
            cache[(r["case_id"], r["redactor"], r["model"])] = r
    clf = None
    with cache_path.open("a", encoding="utf-8") as out:
        for cid in sorted(case_ids):
            r = redacted[cid]
            key = (cid, r["redactor"], model)
            if key in cache:
                continue
            if not r["send_ok"] or RT.no_request_text(r):
                p = {"folder": "F9", "urgent": False, "confidence": "low", "reason": "not sent"}
            else:
                clf = clf or RT.build_classifier()
                if clf is None:
                    sys.exit("No API key yet - nothing to evaluate against.")
                try:
                    p = clf.classify(r)
                except Exception as exc:
                    print(f"  {cid}: {type(exc).__name__} - skipped, re-run to retry", file=sys.stderr)
                    continue
            p.update(case_id=cid, redactor=r["redactor"], model=model)
            out.write(json.dumps(p, ensure_ascii=False) + "\n")
            cache[key] = p
    return {cid: p for (cid, red, m), p in cache.items() if m == model and red == redacted[cid]["redactor"]}


def report(name: str, gold: dict, pred: dict, folders: dict) -> None:
    ids = [c for c in gold if c in pred]
    if not ids:
        print(f"\n== {name}: no predictions yet")
        return
    # What the router actually does: low confidence goes to A Rever.
    routed = {c: pred[c]["folder"] if pred[c].get("confidence") in ("high", "medium") else "F9" for c in ids}
    raw_ok = sum(pred[c]["folder"] == gold[c]["folder"] for c in ids)
    routed_ok = sum(routed[c] == gold[c]["folder"] for c in ids)
    to_review = sum(routed[c] == "F9" for c in ids)
    print(f"\n== {name}: {len(ids)} cases")
    print(f"  model's first choice correct : {raw_ok}/{len(ids)} = {100 * raw_ok / len(ids):.1f}%")
    print(f"  as routed (low conf -> A Rever): {routed_ok}/{len(ids)} = {100 * routed_ok / len(ids):.1f}%"
          f"   sent to A Rever: {to_review} ({100 * to_review / len(ids):.1f}%)")
    print(f"  {'folder':<30}{'recall':>12}{'precision':>12}")
    for fid in sorted(folders):
        tp = sum(gold[c]["folder"] == fid and routed[c] == fid for c in ids)
        g = sum(gold[c]["folder"] == fid for c in ids)
        p = sum(routed[c] == fid for c in ids)
        if g or p:
            print(f"  {folders[fid]:<30}{f'{tp}/{g}':>12}{f'{tp}/{p}':>12}")
    missed = Counter(folders[routed[c]] for c in ids if gold[c]["folder"] == MONEY and routed[c] != MONEY)
    print(f"  refund cases filed elsewhere: {dict(missed) or 'none'}")
    tu = sum(gold[c]["urgent"] and bool(pred[c].get("urgent")) for c in ids)
    gu = sum(gold[c]["urgent"] for c in ids)
    pu = sum(bool(pred[c].get("urgent")) for c in ids)
    print(f"  urgent flag: caught {tu}/{gu}, flagged {pu} in total")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["discovery", "test", "both"], default="both")
    args = ap.parse_args()
    cfg = RT.load_config()
    sets = gold_sets()
    wanted = [s for s in ("discovery", "test") if args.set in (s, "both") and s in sets]
    if not wanted:
        print("no labels found (data/06_labels.json or data/test_labels.csv)", file=sys.stderr)
        return 1
    pred = predict(cfg, set().union(*(sets[s] for s in wanted)))
    for s in wanted:
        report(s, sets[s], pred, cfg["folders"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
