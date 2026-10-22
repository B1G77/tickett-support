#!/usr/bin/env python3
"""File the INBOX leftovers: mail that is on the server but was never in the
POP archive (it sat in the old hand-made event folders, which POP never
downloads), so it was never labelled.

  --fetch   read-only: download what is in the INBOX and put it through the
            archive pipeline - triage, cases, redaction (data/11_left_*.jsonl)
  (label)   session_label.py --set left --show N / --save N "codes"
  --apply   move every labelled case, and the non-customer mail, into its
            folder and record it for the router, so follow-ups join the thread
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone

import backfill as B
import cases as K
import extract as E
import redactor as RD
import router as RT
import triage as T

DATA = B.DATA
MESSAGES = DATA / "11_left_messages.jsonl"
CASES = DATA / "11_left_cases.jsonl"
QUARANTINE = DATA / "11_left_quarantine.jsonl"
REDACTED = DATA / "11_left_redacted.jsonl"
LABELS = DATA / "11_left_labels.jsonl"


def dump(path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def fetch(cfg: dict) -> None:
    box = RT.connect(cfg)
    box.select(box.INBOX, readonly=True)
    recs = []
    for uid in box.uids():
        raw = box.fetch(uid)
        u = uid.decode() if isinstance(uid, bytes) else str(uid)
        rec = E.record_from_bytes(raw, f"imap:{u}")
        rec["key"] = rec.get("message_id") or "sha1:" + hashlib.sha1(raw).hexdigest()
        rec["bucket"], rec["bucket_reason"] = T.classify(rec)
        recs.append(rec)
    box.logout()
    keep = [r for r in recs if r["bucket"] in K.ANALYSE]
    quar = [r for r in recs if r["bucket"] not in K.ANALYSE]
    cases = K.build_cases(keep)
    # Public names (artists, venues) are learnt from the whole archive, as for the archive itself.
    red = RD.Redactor(B.load(B.MESSAGES) + recs, B.load(B.CASES) + cases)
    by_id = {r["id"]: r for r in recs}
    rows = [RD.redact_case(red, c, by_id)[0] for c in cases]
    dump(MESSAGES, recs), dump(CASES, cases), dump(QUARANTINE, quar), dump(REDACTED, rows)
    print(f"{len(recs)} messages in the INBOX -> {len(cases)} cases + {len(quar)} non-customer "
          f"{dict(Counter(r['bucket'] for r in quar))}; blocked by the leak scan: {sum(not r['send_ok'] for r in rows)}")


def work(cases: list, msgs: list, quar: list, labels: list, today: str) -> tuple[list[tuple], int]:
    """(message, folder, urgent, phishing, reason) for everything to file, and how many cases lack a label."""
    dec = B.session_decisions(labels, cases, today)
    by_id = {m["id"]: m for m in msgs}
    items, missing = [], 0
    for c in cases:
        d = dec.get(c["case_id"])
        if not d:
            missing += 1
            continue
        items += [(by_id[i], d["folder"], d["urgent"], False, d["reason"]) for i in c["message_ids"] if i in by_id]
    items += [(m, "F8", False, m["bucket"] == "phishing", m["bucket_reason"]) for m in quar]
    return items, missing


def apply(cfg: dict) -> None:
    items, missing = work(B.load(CASES), B.load(MESSAGES), B.load(QUARANTINE), B.load(LABELS),
                          datetime.now(timezone.utc).date().isoformat())
    if missing:
        sys.exit(f"{missing} cases are not labelled yet - finish session_label.py --set left first")
    sd = cfg["state_dir"]
    lock = open(sd / "router.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)   # the router loop skips its passes meanwhile
    state = RT.State(sd / "router.db")
    box = RT.connect(cfg)
    folders = box.ensure_folders(cfg["folders"])
    box.select(box.INBOX, readonly=False)
    moved, skipped = Counter(), 0
    for m, fid, urgent, phishing, why in items:
        prev = state.get(m["key"])
        if prev and prev["moved_at"]:
            uids = []
        elif m.get("message_id"):
            uids = box.find(m["message_id"])
        else:   # no Message-ID: reuse the fetched UID, after checking it still holds the same message
            uid = m["id"].split(":", 1)[1]
            try:
                same = "sha1:" + hashlib.sha1(box.fetch(uid)).hexdigest() == m["key"]
            except Exception:
                same = False
            uids = [uid] if same else []
        if not uids:
            skipped += 1
            continue
        for uid in uids:
            if urgent:
                box.flag(uid)
            box.move(uid, folders[fid])
        state.record(m["key"], RT.refs(m), RT.Decision(fid, urgent, "leftover", "session", why, phishing), moved=True)
        moved[cfg["folders"][fid]] += 1
    box.logout()
    print(f"filed {sum(moved.values())}, skipped {skipped} (already filed or no longer in the INBOX)")
    for name, n in sorted(moved.items()):
        print(f"  {n:>5}  {name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    cfg = RT.load_config()
    fetch(cfg) if args.fetch else apply(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
