#!/usr/bin/env python3
"""Build a local review page: each email that got a reply draft, next to the draft.

Reads the drafts table (router.db) and fetches each original email from its
folder (or 10 Respondidos) over IMAP. Writes one self-contained HTML file to
~/lebillet-data/report/rascunhos.html. It holds customer data, so it stays
on this Mac: never publish it, never copy it into ~/Documents or git.

Usage: drafts_page.py
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

import cases as K
import drafts as DR
import extract as E
import router as RT

OUT = Path.home() / "lebillet-data" / "report" / "rascunhos.html"
PAGE = Path(__file__).resolve().parent.parent / "config" / "drafts_page.html"


def collect(cfg: dict) -> list[dict]:
    state = RT.State(cfg["state_dir"] / "router.db")
    DR.init(state)
    rows = state.db.execute("SELECT message_id, folder, subject, note, body, created_at, sent_at, kept "
                            "FROM drafts ORDER BY created_at").fetchall()
    box = RT.connect(cfg)
    folders = box.ensure_folders(RT.folder_names(cfg))
    items = []
    for mid, fid, subject, note, body, created, sent, kept in rows:
        rec = None
        for enc in (folders.get(fid), folders[RT.ANSWERED]):
            if not enc:
                continue
            box.select(enc, readonly=True)
            if uids := box.find(mid):
                rec = E.record_from_bytes(box.fetch(uids[0]), "imap:page")
                break
        full = rec.get("body_text", "") if rec else ""
        items.append({
            "folder": cfg["folders"].get(fid, fid), "fid": fid, "subject": subject or "(sem assunto)",
            "from": f"{rec.get('from_name', '')} <{rec.get('from_email', '')}>".strip() if rec else "",
            "date": (rec.get("date", "") if rec else "")[:16].replace("T", " "),
            "text": K.strip_quotes(full, K.is_forward(subject or "", full)) if rec else "",
            "full": full[:12000], "note": note or "", "draft": body or "",
            "created": (created or "")[:16].replace("T", " "), "sent": bool(sent),
            "kept": kept, "found": rec is not None,
        })
    box.logout()
    return items


def build(items: list[dict]) -> str:
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    stamp = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")
    page = PAGE.read_text(encoding="utf-8")
    return page.replace("{{DATA}}", data).replace("{{STAMP}}", html.escape(stamp))


def main() -> int:
    items = collect(RT.load_config())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(items), encoding="utf-8")
    OUT.chmod(0o600)
    print(f"{len(items)} drafts -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
