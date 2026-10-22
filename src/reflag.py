#!/usr/bin/env python3
"""Re-check every red flag on the server against the legal-only rule.

Most flags came over with the old mail (flagged by hand in the past); others
were set by the router under the earlier, wider urgent rule. Every flagged
message in INBOX, the router folders and 10 Respondidos is checked with the
router's current urgent_hint on the new text only. Where that does not fire,
the flag is removed, the decision updated, and the Message-ID and folder
appended to unflagged.jsonl, so it can be put back. Nothing is moved.

Usage: reflag.py --plan | --apply
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

import extract as E
import router as RT
from imap_util import Mailbox


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="count what would be unflagged")
    g.add_argument("--apply", action="store_true", help="remove the flags")
    args = ap.parse_args()
    cfg = RT.load_config()
    sd = cfg["state_dir"]
    state, log = RT.State(sd / "router.db"), RT.Log(sd / "router.log")
    box = RT.connect(cfg)
    names = RT.folder_names(cfg)
    folders = box.ensure_folders(names)
    kept, cleared = Counter(), Counter()
    with (sd / "unflagged.jsonl").open("a", encoding="utf-8") as undo:
        for key, enc in [("INBOX", Mailbox.INBOX)] + list(folders.items()):
            name = names.get(key, key)
            box.select(enc, readonly=not args.apply)
            for uid in box.flagged():
                rec = E.record_from_bytes(box.fetch(uid), "imap:reflag")
                if RT.urgent_hint(RT.new_text(rec)):
                    kept[name] += 1
                    continue
                cleared[name] += 1
                if args.apply:
                    box.unflag(uid)
                    mid = rec.get("message_id", "")
                    undo.write(json.dumps({"message_id": mid, "folder": name, "at": RT.now()}) + "\n")
                    if mid and state.get(mid):
                        state.mark(mid, urgent=0)
    box.logout()
    for name in sorted(set(kept) | set(cleared)):
        print(f"{cleared[name]:5} {'unflagged' if args.apply else 'to unflag'}  {kept[name]:4} kept  {name}")
    print(f"total: {sum(cleared.values())} {'unflagged' if args.apply else 'to unflag'}, "
          f"{sum(kept.values())} kept")
    if args.apply:
        log("reflag", f"red flags removed: {sum(cleared.values())}, kept {sum(kept.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
