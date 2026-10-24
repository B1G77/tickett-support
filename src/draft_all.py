#!/usr/bin/env python3
"""A reply draft for every open email: customer mail in folders 1-5, 9 and 0 Por Responder that is not
answered and has no usable draft (none, or an untouched empty one).

Each is drafted exactly like the router does (drafts.prepare and drafts.save: redaction, rulebook, flags,
the finance Bcc, relayed mail on the dashboard only); when no draft can be written it gets an empty one.
The draft's category is the email's real topic (drafts.category). Holds the router's lock while it runs,
so the router waits. Nothing is ever sent from here.

Usage: draft_all.py --plan [--since YYYY-MM-DD] | --apply --limit N   (the mailbox; drafts also go in Drafts)
       draft_all.py --db-only --plan | --apply --limit N            (no mailbox: the open hand-offs in router.db)
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import sys
from collections import Counter
from datetime import date

import answered as A
import classify as C
import draft_backlog as BL
import drafts as DR
import extract as E
import ledger as LG
import router as RT
import triage as T

OPEN = DR.DRAFT_FOLDERS + (RT.HANDOFF,)


def _draft_row(state, mid: str) -> dict | None:
    cur = state.db.execute("SELECT * FROM drafts WHERE message_id=?", (mid,))
    r = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], r)) if r else None


def needs_draft(state, mid: str) -> bool:
    """Not answered, and no draft yet or only an untouched empty one."""
    if state.db.execute("SELECT 1 FROM replies WHERE message_id=? AND answered_at IS NOT NULL", (mid,)).fetchone():
        return False
    row = _draft_row(state, mid)
    return row is None or DR.placeholder(row)


def _urgent(state, mid: str) -> bool:
    row = state.db.execute("SELECT urgent FROM decisions WHERE message_id=?", (mid,)).fetchone()
    return bool(row and row[0])


def from_mailbox(box, state, cfg: dict, since: str | None = None,
                 skipped: Counter | None = None) -> list[tuple[dict, RT.Decision, str]]:
    """(email, decision for its draft, triage reason) for every open email in the mailbox lacking a draft;
    the newest message of each conversation, since `since` (default route_since). What is left out is counted
    in `skipped`. Only reads the mailbox. The category is the folder the email sits in; for 0 Por Responder,
    the topic it was handed off from (drafts.category)."""
    DR.init(state)
    LG.init(state)
    since = since or cfg.get("route_since")
    skipped = Counter() if skipped is None else skipped
    names = RT.folder_names(cfg)
    existing, found = box.folders(), []
    for fid, enc in box.encoded({f: names[f] for f in OPEN if f in names}).items():
        if enc not in existing:
            continue
        box.select(enc, readonly=True)
        flagged = set(box.flagged())
        uids = box.uids(since=since)
        if since:
            skipped[f"before {since}"] += len(box.uids()) - len(uids)
        for uid in uids:
            raw = box.fetch(uid)
            rec = E.record_from_bytes(raw, "imap:draft_all")
            rec["message_id"] = rec.get("message_id") or "sha1:" + hashlib.sha1(raw).hexdigest()
            rec["_fid"], rec["_urgent"] = fid, uid in flagged
            found.append(rec)
    todo = []
    for rec in BL.newest_per_conversation(found):
        bucket, why = T.classify(rec)
        mid = rec["message_id"]
        if bucket != "customer":
            skipped["newest message is not a customer email"] += 1
            continue
        if not needs_draft(state, mid):
            skipped["already answered or drafted"] += 1
            continue
        cat = DR.category(state, mid) if rec["_fid"] == RT.HANDOFF else C.current_folder(rec["_fid"])
        todo.append((rec, RT.Decision(cat, rec["_urgent"] or _urgent(state, mid), "draft_all", "high", ""), why))
    return todo


def from_db(state) -> list[tuple[dict, RT.Decision, str]]:
    """The open hand-offs lacking a draft, as emails rebuilt from the text stored with them (no mailbox)."""
    DR.init(state)
    LG.init(state)
    cur = state.db.execute("SELECT * FROM handoffs WHERE answered_at IS NULL "
                           "ORDER BY COALESCE(NULLIF(received_at, ''), created_at)")
    cols = [c[0] for c in cur.description]
    todo = []
    for h in (dict(zip(cols, r)) for r in cur.fetchall()):
        mid = h["message_id"]
        if not needs_draft(state, mid):
            continue
        rec = DR.handoff_rec(h)
        _, why = T.classify(rec)
        todo.append((rec, RT.Decision(DR.category(state, mid), _urgent(state, mid), "draft_all", "high", ""), why))
    return todo


def counts(todo, cfg: dict) -> dict[str, int]:
    return dict(sorted(Counter(cfg["folders"].get(d.folder, d.folder) for _, d, _ in todo).items()))


def draft(todo, box, drafts_folder: str, state, drafter, redactor, cfg: dict, log) -> int:
    """Draft each one as the router does; box None keeps the drafts on the dashboard only. Returns how many
    real drafts were written (the others got an empty one)."""
    done = 0
    for rec, d, why in todo:
        mid = rec["message_id"]
        if not needs_draft(state, mid):   # answered, sent or worked on since the list was made
            log(mid, "no draft - handled meanwhile")
            continue
        out = DR.prepare(rec, mid, d, drafter, redactor, cfg, log, why)
        if out is not None and DR.save(box, drafts_folder, rec, mid, d, state, out, drafter, cfg, log):
            done += 1
        elif _draft_row(state, mid) is None:
            DR.save_empty(state, rec, mid, d, log, why)
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="count the open emails without a draft; changes nothing")
    g.add_argument("--apply", action="store_true", help="write the drafts")
    ap.add_argument("--db-only", action="store_true", help="no mailbox: draft the open hand-offs from router.db")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="mailbox mode: start here instead of route_since")
    ap.add_argument("--limit", type=int, metavar="N", help="with --apply: refuse when more than N emails would be "
                    "drafted (each one is a model call)")
    args = ap.parse_args(argv)
    if args.since:
        try:
            date.fromisoformat(args.since)
        except ValueError:
            ap.error("--since takes a date as YYYY-MM-DD")
    if args.apply and args.limit is None:
        ap.error("--apply needs --limit N (the most emails it may draft; run --plan to see how many)")
    cfg = RT.load_config()
    sd = cfg["state_dir"]
    lock = open(sd / "router.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)   # waits for the router's current cycle to finish
    state, log = RT.State(sd / "router.db"), RT.Log(sd / "router.log")
    box = None if args.db_only else RT.connect(cfg)
    try:
        skipped = Counter()
        todo = from_db(state) if args.db_only else from_mailbox(box, state, cfg, args.since, skipped)
        print(f"{len(todo)} open emails without a draft" + (" (hand-offs in router.db)" if args.db_only else "") + ":")
        for name, n in counts(todo, cfg).items():
            print(f"  {n:4}  {name}")
        for why, n in sorted(skipped.items()):
            print(f"  skipped {n:4}  {why}")
        if args.apply and len(todo) > args.limit:
            print(f"not drafting: {len(todo)} emails is more than --limit {args.limit}")
            return 2
        if args.apply and todo:
            RT.load_secret_env()
            drafter = RT.build_drafter({**cfg, "drafts": {"enabled": True}}, object(), log)
            if drafter is None:
                sys.exit("drafter unavailable - see router.log")
            drafts_folder = A._special(box, "\\Drafts", "Drafts") if box else ""
            done = draft(todo, box, drafts_folder, state, drafter, RT.build_redactor(), cfg, log)
            print(f"drafted {done} of {len(todo)}; the rest got an empty draft to write on the dashboard")
            log("draft_all", f"drafts written: {done} of {len(todo)}")
    finally:
        if box is not None:
            box.logout()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
