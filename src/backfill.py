#!/usr/bin/env python3
"""Stage 9 - file the last 12 months of old mail into the folders.

Three steps, each safe to re-run:
  --classify   ask Claude about every case with activity since `backfill_since`
               (redacted text only; results are cached, so a re-run costs nothing)
  --plan       read-only on the server: which messages are still there, which
               POP already removed, and how much would be uploaded vs. the quota
  --apply      move the messages still on the server; upload the missing ones
               into their folder, marked as read, with their original date
               (--no-upload: only move what is already on the server)

Without an API key, --plan/--apply take the folders from the in-session read
instead (--labels session, data/10_session_labels.jsonl); --all widens the
window from `backfill_since` to the whole archive.

Every filed message is recorded in the router's database, so a customer who
replies to an old conversation lands in the same folder. Old phishing is filed
too, and the router's 30-day rule then sends it to Trash.
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import imaplib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import extract as E
import router as RT

DATA = Path(__file__).resolve().parent.parent / "data"
CASES = DATA / "03_cases.jsonl"
REDACTED = DATA / "04_redacted.jsonl"
MESSAGES = DATA / "02_triaged.jsonl"
QUARANTINE = DATA / "03_quarantine.jsonl"
SESSION = DATA / "10_session_labels.jsonl"
QUOTA_HEADROOM = 0.8   # never fill more than 80% of the free space
URGENT_DAYS = 30       # old cases keep the red flag only if they are still this recent


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def cases_in_window(cfg: dict, everything: bool = False) -> list[dict]:
    return [c for c in load(CASES) if everything or c["last_date"][:10] >= cfg["backfill_since"]]


def session_decisions(labels: list[dict], cases: list[dict], today: str) -> dict:
    """The in-session labels in the same shape as the API cache. A red flag on
    a conversation that ended months ago is noise, so urgency is kept only for
    cases active in the last URGENT_DAYS."""
    cutoff = (date.fromisoformat(today) - timedelta(days=URGENT_DAYS)).isoformat()
    last = {c["case_id"]: c["last_date"][:10] for c in cases}
    return {r["case_id"]: {"folder": r["folder"], "confidence": "session", "reason": f"session label {r['code']}",
                           "urgent": bool(r["urgent"]) and last.get(r["case_id"], "") >= cutoff}
            for r in labels}


def cache_file(cfg: dict) -> Path:
    return cfg["state_dir"] / "backfill_classified.jsonl"


def load_cache(cfg: dict) -> dict:
    p = cache_file(cfg)
    return {r["case_id"]: r for r in load(p)} if p.exists() else {}


def classify(cfg: dict) -> None:
    cfg["state_dir"].mkdir(parents=True, exist_ok=True)
    redacted = {r["case_id"]: r for r in load(REDACTED)}
    done = load_cache(cfg)
    todo = [c for c in cases_in_window(cfg) if c["case_id"] not in done]
    print(f"{len(done)} cases already classified, {len(todo)} to go", file=sys.stderr)
    clf = RT.build_classifier() if todo else None
    if todo and clf is None:
        sys.exit("No API key yet - use the in-session labels (session_label.py) instead.")
    failed = 0
    with cache_file(cfg).open("a", encoding="utf-8") as out:
        for n, c in enumerate(todo, 1):
            r = redacted[c["case_id"]]
            if not r["send_ok"]:
                d = {"folder": "F9", "urgent": False, "confidence": "low", "reason": "not sent - leak check"}
            elif RT.no_request_text(r):
                d = {"folder": "F9", "urgent": False, "confidence": "low", "reason": "no request text"}
            else:
                try:
                    d = clf.classify(r)
                except Exception as exc:   # left out of the cache: retried on the next run
                    failed += 1
                    print(f"  {c['case_id']}: {type(exc).__name__}", file=sys.stderr)
                    continue
                if d.get("confidence") not in ("high", "medium"):
                    d = {**d, "suggested": d["folder"], "folder": "F9"}
            d["urgent"] = bool(d.get("urgent")) or RT.urgent_hint(c["subject"] + "\n" + c["customer_text"])
            d.update(case_id=c["case_id"], redactor=r["redactor"])
            out.write(json.dumps(d, ensure_ascii=False) + "\n")
            out.flush()
            if n % 100 == 0:
                print(f"  {n}/{len(todo)}", file=sys.stderr)
    cache = load_cache(cfg)
    tokens = sum(r.get("usage", {}).get("in", 0) + r.get("usage", {}).get("out", 0) for r in cache.values())
    print(f"classified {len(cache)} cases ({failed} failed, re-run to retry), ~{tokens:,} tokens", file=sys.stderr)
    for fid, n in sorted(Counter(r["folder"] for r in cache.values()).items()):
        print(f"  {n:>5}  {cfg['folders'][fid]}", file=sys.stderr)


def work_items(cfg: dict, source: str = "api", everything: bool = False) -> tuple[list[tuple], int]:
    """(message record, folder, urgent, phishing, decision) for everything to file."""
    if source == "session":
        cache = session_decisions(load(SESSION), load(CASES), datetime.now(timezone.utc).date().isoformat())
    else:
        cache = load_cache(cfg)
    msgs = {m["id"]: m for m in load(MESSAGES)}
    items, unclassified = [], 0
    for c in cases_in_window(cfg, everything):
        d = cache.get(c["case_id"])
        if not d:
            unclassified += 1
            continue
        for mid in c["message_ids"]:
            if mid in msgs:
                items.append((msgs[mid], d["folder"], d["urgent"], False, d))
    for m in load(QUARANTINE):
        if everything or (m.get("date") or "")[:10] >= cfg["backfill_since"]:
            phishing = m["bucket"] == "phishing"
            items.append((m, "F8", False, phishing, {"confidence": "high", "reason": m["bucket_reason"]}))
    return items, unclassified


def key_of(m: dict, raw: bytes | None = None) -> str:
    return m.get("message_id") or "sha1:" + hashlib.sha1(raw or E.read_emlx(E.MAILBOX / m["id"])).hexdigest()


def quota_free_kb(box) -> int | None:
    q = box.quota()
    text = " ".join(x.decode() if isinstance(x, bytes) else str(x) for part in (q or []) for x in (part or []))
    m = re.search(r"STORAGE (\d+) (\d+)", text)
    return int(m.group(2)) - int(m.group(1)) if m else None


def plan(cfg: dict, apply: bool = False, upload: bool = True, source: str = "api", everything: bool = False) -> None:
    items, unclassified = work_items(cfg, source, everything)
    if unclassified:
        print(f"{unclassified} cases not classified yet - run --classify first", file=sys.stderr)
    state = RT.State(cfg["state_dir"] / "router.db")
    box = RT.connect(cfg)
    folders = box.ensure_folders(cfg["folders"]) if apply else box.encoded(cfg["folders"])
    box.select(box.INBOX, readonly=not apply)
    to_move, to_upload, already = [], [], 0
    for item in items:
        m = item[0]
        k = key_of(m)
        prev = state.get(k)
        if prev and prev["moved_at"]:
            already += 1
            continue
        uids = box.find(m["message_id"]) if m.get("message_id") else []
        (to_move if uids else to_upload).append((item, k, uids))
    upload_kb = sum(len(E.read_emlx(E.MAILBOX / it[0]["id"])) for it, _, _ in to_upload) // 1024
    free = quota_free_kb(box)
    print(f"to file: {len(items)} messages | already filed: {already} | still on the server: {len(to_move)} "
          f"| removed by POP, to upload: {len(to_upload)} ({upload_kb / 1024:.0f} MB)")
    print("free quota:", f"{free / 1024:.0f} MB" if free is not None else "not reported by the server")
    by_folder = Counter(cfg["folders"][it[1]] for it, _, _ in to_move + to_upload)
    for name, n in sorted(by_folder.items()):
        print(f"  {n:>5}  {name}")
    if not apply:
        box.logout()
        return
    if upload and free is not None and upload_kb > free * QUOTA_HEADROOM:
        box.logout()
        sys.exit("Upload would exceed 80% of the free quota. Re-run with --no-upload, or free space first.")

    for (m, fid, urgent, phishing, d), k, uids in to_move:
        for uid in uids:
            if urgent:
                box.flag(uid)
            box.move(uid, folders[fid])
        state.record(k, RT.refs(m), RT.Decision(fid, urgent, "backfill", d.get("confidence", ""),
                                                d.get("reason", ""), phishing), moved=True)
    if upload:
        for (m, fid, urgent, phishing, d), k, _ in to_upload:
            raw = E.read_emlx(E.MAILBOX / m["id"])
            when = email.utils.parsedate_to_datetime(m["date_raw"]) if m.get("date_raw") else None
            date = imaplib.Time2Internaldate(when.timestamp()) if when else None
            box.append(folders[fid], "(\\Seen \\Flagged)" if urgent else "(\\Seen)", date, raw)
            state.record(k, RT.refs(m), RT.Decision(fid, urgent, "backfill", d.get("confidence", ""),
                                                    d.get("reason", ""), phishing), moved=True)
    box.logout()
    print(f"filed {len(to_move)} moved + {len(to_upload) if upload else 0} uploaded")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--classify", action="store_true")
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--no-upload", action="store_true", help="with --apply: only move what is on the server")
    ap.add_argument("--labels", choices=["api", "session"], default="api", help="where the folders come from")
    ap.add_argument("--all", action="store_true", help="the whole archive, not only since backfill_since")
    args = ap.parse_args()
    cfg = RT.load_config()
    if args.classify:
        classify(cfg)
    else:
        plan(cfg, apply=args.apply, upload=not args.no_upload, source=args.labels, everything=args.all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
