#!/usr/bin/env python3
"""
Fetch new corporate-finance research from Crossref and write data.json.

  python scripts/fetch_papers.py            normal run (what the GitHub Action does)
  python scripts/fetch_papers.py --check    verify every ISSN in config.json
  python scripts/fetch_papers.py --days 30  override the look-back window
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8"))
OUT = ROOT / "data.json"
API = "https://api.crossref.org"
CONTACT = os.environ.get("CROSSREF_EMAIL") or CFG["contact_email"]  # secret in the Action, config.json locally
HEADERS = {"User-Agent": f"cf-research-updates/1.0 (mailto:{CONTACT})"}
SKIP_TITLE = re.compile(
    r"^(issue information|editorial board|front matter|back matter|erratum|corrigendum|retraction|"
    r"announcement|masthead|table of contents|index to volume|title page|editor'?s? (note|introduction)|"
    r"call for papers|list of reviewers|acknowledg)", re.I)


def rx(words):
    """Word-boundary regex from a keyword list; a trailing * matches any suffix."""
    parts = []
    for w in words:
        w = w.lower()
        parts.append(re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w))
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")


THEME_RX = {k: rx(v["keywords"]) for k, v in CFG["themes"].items()}
AREA_RX = {k: rx(v) for k, v in CFG["areas"].items()}
SSRN_RX = rx(CFG["ssrn"]["keywords"])


def get(url, params=None):
    last = None
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=90)
        except requests.RequestException as e:
            last = e
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        last = f"HTTP {r.status_code}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Crossref request failed: {url} ({last})")


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"^\s*abstract\s*[:.]?\s*", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def tag(text):
    t = text.lower()
    themes = [k for k, r in THEME_RX.items() if r.search(t)]
    hits = {k: len(r.findall(t)) for k, r in AREA_RX.items()}
    best = max(hits, key=hits.get)
    return themes, (best if hits[best] > 0 else "General")


def parse(item, code):
    title = clean((item.get("title") or [""])[0])
    if not title or SKIP_TITLE.search(title):
        return None
    authors = ", ".join(
        " ".join(p for p in (a.get("given"), a.get("family")) if p)
        for a in item.get("author", []) if a.get("family") or a.get("given")
    ) or "—"
    ab = clean(item.get("abstract", ""))
    limit = CFG.get("abstract_chars", 700)
    if len(ab) > limit:
        ab = ab[:limit].rsplit(" ", 1)[0] + "…"
    themes, area = tag(title + " " + ab)
    return {
        "j": code, "t": title, "d": item["created"]["date-time"][:10],
        "a": area, "th": themes, "au": authors, "ab": ab,
        "url": item.get("URL") or f"https://doi.org/{item['DOI']}", "doi": item["DOI"].lower(),
    }


def works(issn, since, until, rows, max_pages):
    cursor = "*"
    for _ in range(max_pages):
        data = get(f"{API}/journals/{issn}/works", {
            "filter": f"from-created-date:{since},until-created-date:{until},type:journal-article",
            "rows": rows, "cursor": cursor,
            "select": "DOI,title,author,abstract,created,URL",
        })
        if data is None:
            print(f"  WARNING: ISSN {issn} not found on Crossref - check config.json", flush=True)
            return
        msg = data["message"]
        items = msg.get("items", [])
        yield from items
        cursor = msg.get("next-cursor")
        if len(items) < rows or not cursor:
            return


def fetch_journals(since, until):
    out, seen = [], set()
    for code, j in CFG["journals"].items():
        n = 0
        for it in works(j["issn"], since, until, 200, 10):
            p = parse(it, code)
            if not p or p["doi"] in seen:
                continue
            if j.get("require_match") and not p["th"] and p["a"] == "General":
                continue
            seen.add(p["doi"])
            out.append(p)
            n += 1
        print(f"  {code:5s} {n:3d} new", flush=True)
        time.sleep(1)
    return out


def fetch_ssrn(since, until):
    cfg = CFG["ssrn"]
    out, n_all = [], 0
    for it in works(cfg["issn"], since, until, 1000, cfg.get("max_pages", 12)):
        n_all += 1
        p = parse(it, "SSRN")
        if p and SSRN_RX.search(p["t"].lower()):
            out.append(p)
    print(f"  SSRN  {len(out):3d} kept of {n_all} new DOIs", flush=True)
    out.sort(key=lambda p: p["d"], reverse=True)
    return out[: cfg.get("max_items", 200)]


def window(args):
    today = dt.date.today()
    if args.days:
        return today - dt.timedelta(days=args.days), today
    prev = None
    if OUT.exists():
        try:
            prev = (json.loads(OUT.read_text(encoding="utf-8")).get("window") or {}).get("to")
        except Exception:
            prev = None
    if prev:
        since = dt.date.fromisoformat(prev) - dt.timedelta(days=CFG.get("overlap_days", 2))
    else:
        since = today - dt.timedelta(days=CFG.get("window_days", 14))
    since = max(since, today - dt.timedelta(days=CFG.get("max_window_days", 45)))
    return since, today


def check():
    rows = list(CFG["journals"].items()) + [("SSRN", CFG["ssrn"])]
    for code, j in rows:
        d = get(f"{API}/journals/{j['issn']}")
        name = d["message"]["title"] if d else "NOT FOUND - fix this ISSN"
        print(f"{code:5s} {j['issn']:10s} {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify ISSNs against Crossref")
    ap.add_argument("--days", type=int, help="look back this many days instead of the saved window")
    args = ap.parse_args()
    if args.check:
        check()
        return 0
    since, until = window(args)
    print(f"Window {since} to {until}")
    print("Journals:")
    pubs = fetch_journals(since.isoformat(), until.isoformat())
    ssrn = fetch_ssrn(since.isoformat(), until.isoformat()) if CFG["ssrn"].get("enabled", True) else []
    pubs.sort(key=lambda p: (p["d"], p["j"]), reverse=True)
    data = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "window": {"from": since.isoformat(), "to": until.isoformat()},
        "repo": CFG.get("repo_url", ""),
        "journals": {k: v["name"] for k, v in CFG["journals"].items()},
        "themes": {k: {"n": v["name"], "d": v["description"], "c": v["color"]} for k, v in CFG["themes"].items()},
        "areas": list(CFG["areas"].keys()),
        "pubs": pubs,
        "ssrn": ssrn,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {OUT.name}: {len(pubs)} articles, {len(ssrn)} SSRN papers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
