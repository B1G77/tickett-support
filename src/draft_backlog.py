#!/usr/bin/env python3
"""One-off: write reply drafts for the mail that was waiting before drafting went live.

Looks at the same folders as the "Por responder" digest (folders 1-7, mail
since route_since), keeps the newest message of each conversation, and
drafts it exactly like the router does: same rules (drafts.qualifies), same
redaction, same Drafts folder, same drafts table. Red-flagged (legal) mail
is skipped. Holds the router's lock while it runs, so the router waits.

Usage: draft_backlog.py --plan | --apply
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import sys
from collections import Counter
from datetime import datetime

import answered as A
import drafts as DR
import extract as E
import router as RT
import triage as T


def _key(rec: dict) -> str:
    refs = rec.get("references") or []
    return refs[0] if refs else (rec.get("in_reply_to") or rec.get("message_id", ""))


def _when(rec: dict) -> float:
    """Seconds since the epoch; 0 when the date is missing or unreadable."""
    try:
        return datetime.fromisoformat(rec.get("date", "")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def newest_per_conversation(recs: list[dict]) -> list[dict]:
    """The newest message of each conversation, oldest conversation first."""
    newest: dict[str, dict] = {}
    for r in recs:
        k = _key(r)
        if k not in newest or _when(r) > _when(newest[k]):
            newest[k] = r
    return sorted(newest.values(), key=_when)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="count what would be drafted; no API calls")
    g.add_argument("--apply", action="store_true", help="write the drafts")
    args = ap.parse_args()
    cfg = RT.load_config()
    sd = cfg["state_dir"]
    lock = open(sd / "router.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)   # waits for the router's current cycle to finish
    state, log = RT.State(sd / "router.db"), RT.Log(sd / "router.log")
    box = RT.connect(cfg)
    folders = box.ensure_folders(cfg["folders"])
    skipped, found = Counter(), []
    for fid in DR.DRAFT_FOLDERS:
        box.select(folders[fid], readonly=True)
        flagged = set(box.flagged())
        for uid in box.uids(since=cfg.get("route_since")):
            raw = box.fetch(uid)
            rec = E.record_from_bytes(raw, "imap:backlog")
            rec["message_id"] = rec.get("message_id") or "sha1:" + hashlib.sha1(raw).hexdigest()
            rec["_fid"], rec["_urgent"] = fid, uid in flagged
            found.append(rec)
    todo = []
    for rec in newest_per_conversation(found):
        bucket, why = T.classify(rec)
        d = RT.Decision(rec["_fid"], rec["_urgent"], "backlog", "high", "")
        if DR.qualifies(d, bucket, state, rec["message_id"], why):
            todo.append((rec, d))
        else:
            skipped["flagged" if rec["_urgent"] else "already drafted" if DR.has_draft(state, rec["message_id"])
                    else f"not drafted ({bucket}: {why[:40]})"] += 1
    by_folder = Counter(cfg["folders"][d.folder] for _, d in todo)
    print(f"{len(found)} open messages, {len(found) - len(newest_per_conversation(found))} older ones in the same "
          f"conversation, {len(todo)} to draft:")
    for name, n in sorted(by_folder.items()):
        print(f"  {n:4}  {name}")
    for why, n in skipped.most_common():
        print(f"  skip {n:3}  {why}")
    if args.apply:
        RT.load_secret_env()   # the router gets the API key via build_classifier; this script must load it
        drafter = RT.build_drafter({**cfg, "drafts": {"enabled": True}}, object(), log)
        if drafter is None:
            box.logout()
            sys.exit("drafter unavailable - see router.log")
        redactor = RT.build_redactor()
        drafts_folder = A._special(box, "\\Drafts", "Drafts")
        done = 0
        for i, (rec, d) in enumerate(todo, 1):
            done += DR.try_draft(box, drafts_folder, rec, rec["message_id"], d, state, drafter, redactor, cfg, log)
            print(f"\r{i}/{len(todo)} drafted {done}", end="", flush=True)
        print()
        log("backlog", f"backlog drafts written: {done} of {len(todo)}")
    box.logout()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
