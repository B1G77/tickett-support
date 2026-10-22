#!/usr/bin/env python3
"""Facts for the reply drafter from the public tickett.com site.

Once a day the router crawls every event in the site's sitemap, the event's
ticket packages and the terms, and keeps them in ~/lebillet-data/site/:
  events.json  one record per event, from the page's schema.org Event block and
               its labelled fields, plus the packages (name, what is included,
               batch, price) from the event's ticket-selection page
  terms.txt    the general sales conditions, as text
The ticket-selection page sits under /checkout/, which robots.txt asks crawlers
to skip; the user (the site's owner) allowed reading it once a day, read-only:
nothing is selected or put in a cart. A package page that fails keeps the
event's previous packages. Events no longer listed keep their last record with
listed=false. A failed crawl leaves the previous files alone.

Usage: site_facts.py --crawl
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
import unicodedata
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = "https://tickett.com/"
LANG = "pt-PT"
TERMS_PAGES = (f"{LANG}/termos",)
USER_AGENT = "Tickett-support-drafter/1.0 (support@tickett.com)"
SITE_DIR = Path.home() / "lebillet-data" / "site"
MAX_DESCRIPTION = 600
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_LOC = re.compile(r"<loc>\s*(https://tickett\.com/" + LANG + r"/event/(\d+)/[^<\s]+)\s*</loc>")
_PRODUCT = re.compile(r'class="td-product-name textprod(\d+)"[^>]*>\s*<h6[^>]*>(.*?)</h6>', re.S)
_ROW = r'<tr class="tr-products tiposprod{}">\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>'
_BOX_HEAD = re.compile(r"<h5[^>]*>(.*?)</h5>", re.S)
_BOX_ROW = re.compile(r"<td[^>]*>\s*((?:<span[^>]*>.*?</span>\s*)+)</td>\s*<td[^>]*>(.*?)<a class=\"button-select-product",
                      re.S)


def event_links(sitemap: str) -> list[str]:
    """The Portuguese page of each event in the sitemap, once per event."""
    seen, out = set(), []
    for url, eid in _LOC.findall(sitemap):
        if eid not in seen:
            seen.add(eid)
            out.append(url)
    return out


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _field(name: str, page: str) -> str:
    m = re.search(r'data-name="' + name + r'"[^>]*>.*?fieldValue[^>]*>(.*?)</p>', page, re.S)
    return _text(m.group(1)) if m else ""


def _event_block(page: str) -> dict | None:
    for m in _LD.finditer(page):
        try:
            ld = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for item in (ld.get("@graph", [ld]) if isinstance(ld, dict) else []):
            if isinstance(item, dict) and item.get("@type") == "Event":
                return item
    return None


def parse_event(page: str, url: str) -> dict | None:
    """One event from its page, or None when the page has no usable Event block."""
    ld = _event_block(page)
    if ld is None:
        return None
    place = ld.get("location") or {}
    addr = place.get("address") or {}
    offer = ld.get("offers") or {}
    start = str(ld.get("startDate", ""))
    doors = re.search(r'pillText">[^<]*Portas\s+([^<]+)<', page)
    return {
        "id": (re.search(r"/event/(\d+)/", url) or [None, ""])[1], "url": url,
        "name": html.unescape(str(ld.get("name", ""))).strip(),
        "date": start[:10], "start": start[11:16],
        "doors": doors.group(1).strip() if doors else "",
        "venue": place.get("name", ""), "address": addr.get("streetAddress", ""),
        "city": addr.get("addressLocality", ""), "country": addr.get("addressCountry", ""),
        "age": _field("rating", page), "promoter": _field("promoter", page),
        "description": html.unescape(str(ld.get("description", "")))[:MAX_DESCRIPTION],
        "price_from": str(offer.get("price", "")), "currency": offer.get("priceCurrency", ""),
        "sales_closed": '\\"salesClosed\\":true' in page,
        "packages": [], "listed": True,
    }


def _name_includes(head: str) -> tuple[str, str]:
    small = re.search(r"<small>(.*?)</small>", head, re.S)
    return _text(head[:small.start()] if small else head), _text(small.group(1)) if small else ""


def parse_packages(page: str) -> list[dict]:
    """Each package on the ticket-selection page: name, what it includes, batch and price.
    The site has two layouts: a product table, and one "ticket-box" table per package."""
    out = []
    for pid, head in _PRODUCT.findall(page):
        name, includes = _name_includes(head)
        for batch, price in re.findall(_ROW.format(pid), page, re.S):
            out.append({"name": name, "includes": includes, "batch": _text(batch), "price": _text(price)})
    for box in page.split('class="ticket-box"')[1:]:
        head = _BOX_HEAD.search(box)
        if not head:
            continue
        name, includes = _name_includes(head.group(1))
        for spans, price in _BOX_ROW.findall(box):
            batch = ", ".join(t for t in (_text(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", spans, re.S)) if t)
            out.append({"name": name, "includes": includes, "batch": batch, "price": _text(price)})
    return out


def page_text(page: str) -> str:
    page = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", page)
    return _text(page)


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def _key(name: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", fold(name)) if len(w) >= 4 and not w.isdigit()]
    return words[:2]


def match(text: str, events: list[dict], today: date, limit: int = 3) -> list[dict]:
    """Events the email names: the first two significant words of the event
    name must both appear. Upcoming dates first (soonest first), then past ones."""
    words = set(re.findall(r"\w+", fold(text)))
    hits = [e for e in events if (k := _key(e.get("name", ""))) and all(w in words for w in k)]
    day = today.isoformat()
    upcoming = sorted((e for e in hits if e.get("date", "") >= day), key=lambda e: e["date"])
    past = sorted((e for e in hits if e.get("date", "") < day), key=lambda e: e["date"], reverse=True)
    return (upcoming + past)[:limit]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_events(site_dir: Path = SITE_DIR) -> list[dict]:
    f = site_dir / "events.json"
    return json.loads(f.read_text(encoding="utf-8"))["events"] if f.exists() else []


def load_terms(site_dir: Path = SITE_DIR) -> str:
    f = site_dir / "terms.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def crawl(out_dir: Path = SITE_DIR, get=fetch, pause: float = 1.0) -> int:
    """Fetch everything first; files are only rewritten when the whole crawl worked."""
    old = {e["id"]: e for e in load_events(out_dir)}
    fresh: dict[str, dict] = {}
    for url in event_links(get(BASE + "sitemap.xml")):
        time.sleep(pause)
        e = parse_event(get(url), url)
        if not e:
            continue
        time.sleep(pause)
        try:
            e["packages"] = parse_packages(get(f"{BASE}{LANG}/checkout/{e['id']}"))
        except OSError:
            e["packages"] = old.get(e["id"], {}).get("packages", [])
        fresh[e["id"]] = e
    if not fresh:
        raise RuntimeError("no events parsed - has the site layout changed?")
    terms = []
    for p in TERMS_PAGES:
        time.sleep(pause)
        terms.append(page_text(get(BASE + p)))
    out_dir.mkdir(parents=True, exist_ok=True)
    for eid, e in old.items():
        if eid not in fresh:
            fresh[eid] = {**e, "listed": False}
    _write(out_dir / "events.json", json.dumps(
        {"crawled_at": datetime.now().isoformat(timespec="seconds"), "events": list(fresh.values())},
        ensure_ascii=False, indent=1))
    _write(out_dir / "terms.txt", "\n\n".join(terms))
    return sum(1 for e in fresh.values() if e["listed"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--crawl", action="store_true", required=True)
    ap.parse_args()
    print(f"{crawl()} events listed; files in {SITE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
