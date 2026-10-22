#!/usr/bin/env python3
"""Download the Sent folder, read-only, so past answers can shape reply templates.

The folder is opened with EXAMINE (read-only) and messages are fetched with
BODY.PEEK, so nothing on the server changes. Only the parsed record is kept
(text, headers, attachment metadata) - in data/, outside the repo and iCloud.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import extract as E
import router as RT

OUT = Path(__file__).resolve().parent.parent / "data" / "08_sent.jsonl"


def main() -> int:
    box = RT.connect(RT.load_config())
    folders = box.folders()
    sent = next((n for n, f in folders.items() if "\\Sent" in f), box.prefix + "Sent")
    box.select(sent, readonly=True)
    uids = box.uids()
    ok = 0
    with OUT.open("w", encoding="utf-8") as out:
        for uid in uids:
            try:
                rec = E.record_from_bytes(box.fetch(uid), f"sent:{uid.decode()}")
            except Exception as exc:
                print(f"  uid {uid!r}: {type(exc).__name__}", file=sys.stderr)
                continue
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            ok += 1
    box.logout()
    print(f"{ok} of {len(uids)} sent messages -> {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
