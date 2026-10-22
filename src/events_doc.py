#!/usr/bin/env python3
"""The Tickett team's events document (a Google Doc), as facts for the reply drafter.

The Doc is shared by link, so its Markdown export is read without a Google login.
Parte 1 holds general questions and answers, Parte 2 one block per event, and
"Outras informações" anything else. The grey example text the template put in
some cells counts as empty. The parsed copy is kept in ~/lebillet-data/site/doc.json;
a failed fetch, or a page that no longer has the two parts, keeps the last good copy.

Usage: events_doc.py --fetch   (fetch now and print what was understood)
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SITE_DIR = Path.home() / "lebillet-data" / "site"
DOC_ID = "1DjSfMuDZlguf6EOz_y49E-sr4hnY4p1Npj959_b7tTE"
USER_AGENT = "Tickett-support-drafter/1.0 (support@tickett.com)"
# example text from the template, not an answer
HINTS = {"ativo / adiado / cancelado", "pode pedir reembolso? como?", "tickett / promotor",
         "automático, ou a pedido (e como se pede)",
         "bilheteira no local, estacionamento, acessibilidade, regras de entrada"}
OTHER_INTRO = "Espaço para tudo o que ajude a responder aos clientes"
_ROW = re.compile(r"^\|(.*)\|(.*)\|\s*$")


def export_url(doc_id: str = DOC_ID) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/export?format=md"


def clean(cell: str) -> str:
    # a line break inside a cell arrives as a vertical tab (or &#10;)
    s = cell.replace("\v", " ").replace("&#10;", " ").replace("**", "").replace("\\", "")
    s = re.sub(r"\s+", " ", s).strip()
    return "" if s.lower() in HINTS else s


def _title(line: str) -> str:
    return clean(line.lstrip("#"))


def parse(md: str) -> dict:
    """{"general": [{"topic", "question", "answer"}], "events": [{"name", "fields"}], "other": str}."""
    part, heading = None, None
    general, events, other = [], [], []
    for line in md.split("\n"):   # not splitlines(): it also breaks on the vertical tab
        if line.startswith("# "):
            t = _title(line).lower()
            part = "general" if t.startswith("parte 1") else "events" if t.startswith("parte 2") \
                else "other" if t.startswith("outras") else None
            heading = None
            continue
        if line.startswith("## "):
            heading = _title(line)
            if part == "events":
                events.append({"name": heading, "fields": {}})
            continue
        if part == "other":
            text = clean(line.lstrip(">* "))
            if text and not text.startswith(OTHER_INTRO):
                other.append(text)
            continue
        m = _ROW.match(line)
        if not m or not heading or set(m.group(1).strip()) <= set(":- "):
            continue
        key, value = clean(m.group(1)), clean(m.group(2))
        if part == "general" and value:
            general.append({"topic": heading, "question": key, "answer": value})
        elif part == "events" and events and value:
            events[-1]["fields"][key] = value
    if not general and not events:
        raise ValueError("no Parte 1 or Parte 2 found - has the document's layout changed?")
    return {"general": general, "events": [e for e in events if e["name"].lower() != "novo evento"],
            "other": "\n".join(other)}


def as_prompt(doc: dict) -> str:
    """The Doc as plain text for the drafter: answers only, events without information named as such."""
    lines = ["Perguntas gerais:"]
    lines += [f"- [{g['topic']}] {g['question']} -> {g['answer']}" for g in doc.get("general", [])]
    lines.append("\nEventos:")
    for e in doc.get("events", []):
        if e["fields"]:
            lines.append(f"## {e['name']}")
            lines += [f"{k}: {v}" for k, v in e["fields"].items()]
        else:
            lines.append(f"## {e['name']}: sem informação no documento")
    if doc.get("other"):
        lines += ["\nOutras informações:", doc["other"]]
    return "\n".join(lines)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def load(site_dir: Path = SITE_DIR) -> dict:
    f = site_dir / "doc.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def refresh(site_dir: Path = SITE_DIR, doc_id: str = DOC_ID, get=fetch) -> dict:
    """Fetch and parse; the file is only replaced when both worked."""
    doc = parse(get(export_url(doc_id)))
    doc["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    site_dir.mkdir(parents=True, exist_ok=True)
    tmp = site_dir / "doc.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(site_dir / "doc.json")
    return doc


def diff(old: dict, new: dict) -> list[dict]:
    """What changed between two copies: events by name (novo / removido / alterado when its fields differ),
    plus "Perguntas gerais" when the general answers differ. [] for a first read (empty old)."""
    if not old:
        return []
    before = {e["name"]: e["fields"] for e in old.get("events", [])}
    after = {e["name"]: e["fields"] for e in new.get("events", [])}
    out = [{"kind": "alterado" if name in before else "novo", "name": name}
           for name, fields in after.items() if before.get(name) != fields or name not in before]
    out += [{"kind": "removido", "name": name} for name in before if name not in after]
    if old.get("general") != new.get("general"):
        out.append({"kind": "alterado", "name": "Perguntas gerais"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch", action="store_true", required=True)
    ap.parse_args()
    doc = refresh()
    filled = [e["name"] for e in doc["events"] if e["fields"]]
    empty = [e["name"] for e in doc["events"] if not e["fields"]]
    print(f"{len(doc['general'])} general answers, {len(filled)} events with information, "
          f"{len(empty)} without: {', '.join(empty) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
