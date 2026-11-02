#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Refresh cache/nber_cf_program.json from NBER's own Corporate Finance program listing.

NBER's program page loads its working-paper list from a public JSON listing (newest first, 50 per page).
This script reads the first few pages of that listing, a handful of polite requests per run, identifies
itself honestly, and stops at the first refusal. The cache is only rewritten when the response passes sanity
checks; otherwise the old cache is left untouched and the script exits non-zero.

  python scripts/refresh_nber_cache.py            refresh the cache
  python scripts/refresh_nber_cache.py --dry-run  fetch and check, write nothing
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
CACHE = ROOT / "cache" / "nber_cf_program.json"
NBER = CFG.get("nber", {})
BASE = os.environ.get("NBER_LISTING_BASE", "https://www.nber.org")
LISTING = BASE + "/api/v1/generic_listing/nid/{nid}/contentType/working_paper/search/contentType"
NID = NBER.get("listing_nid", 11556)           # NBER node id of the Corporate Finance program page
MAX_PAGES = int(NBER.get("refresh_pages", 6))  # 50 papers per page, newest first
KEEP_MONTHS = int(NBER.get("refresh_months", 4))
CONTACT = os.environ.get("CROSSREF_EMAIL") or CFG.get("contact_email", "")
HEADERS = {
    "User-Agent": f"cf-research-updates/1.0 (+{CFG.get('repo_url', '')}; mailto:{CONTACT})",
    "Accept": "application/json",
}
TAG = re.compile(r"<[^>]+>")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(TAG.sub("", s or ""))).strip()


def authors(names):
    names = [text(n) for n in names or [] if text(n)]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1] if names else ""


def parse(item):
    m = re.fullmatch(r"/papers/(w\d{4,6})", item.get("url") or "")
    try:
        month = dt.datetime.strptime((item.get("displaydate") or "").strip(), "%B %Y").strftime("%Y-%m")
    except ValueError:
        month = None
    title = text(item.get("title"))
    if not (m and month and title):
        return None
    return {"wp": m.group(1), "issue_month": month, "title": title, "authors": authors(item.get("authors"))}


def fetch_pages(cutoff):
    papers, total = {}, None
    for page in range(1, MAX_PAGES + 1):
        r = requests.get(LISTING.format(nid=NID), params={"page": page, "perPage": 50}, headers=HEADERS, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f"NBER listing page {page}: HTTP {r.status_code} (stopping, not retrying)")
        data = r.json()
        total = data.get("totalResults", total)
        rows = [parse(i) for i in data.get("results") or []]
        rows = [p for p in rows if p]
        if not rows:
            break
        for p in rows:
            papers.setdefault(p["wp"], p)
        print(f"  page {page}: {len(rows)} papers, newest {rows[0]['issue_month']}, oldest {rows[-1]['issue_month']}")
        if min(p["issue_month"] for p in rows) < cutoff:
            break
        time.sleep(2)  # be polite
    return papers, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    today = dt.date.today()
    y, m = today.year, today.month - KEEP_MONTHS
    while m < 1:
        y, m = y - 1, m + 12
    cutoff = f"{y:04d}-{m:02d}"

    print(f"Refreshing NBER Corporate Finance cache (papers issued since {cutoff})")
    try:
        fetched, total = fetch_pages(cutoff)
    except (RuntimeError, requests.RequestException, ValueError) as e:
        raise SystemExit(f"Refusing to update (cache left as is): {e}")

    # Sanity checks: refuse to replace a good cache with something odd.
    if not isinstance(total, int) or total < 1000:
        raise SystemExit(f"Refusing to update: totalResults={total!r} (expected thousands)")
    recent = {k: v for k, v in fetched.items() if v["issue_month"] >= cutoff}
    if not recent:
        raise SystemExit("Refusing to update: no papers parsed in the recent window")
    newest = max(p["issue_month"] for p in recent.values())
    if newest < (today - dt.timedelta(days=75)).strftime("%Y-%m"):
        raise SystemExit(f"Refusing to update: newest paper is {newest}, which looks stale")

    old = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    merged = {p["wp"]: p for p in old.get("papers", []) if p.get("issue_month", "") >= cutoff}
    merged.update(recent)
    papers = sorted(merged.values(), key=lambda p: (p["issue_month"], int(p["wp"][1:])), reverse=True)
    new = sorted(set(merged) - {p["wp"] for p in old.get("papers", [])})

    out = {
        "program_code": NBER.get("program_code", "CF"),
        "source": "NBER Corporate Finance program listing",
        "source_url": "https://www.nber.org/programs-projects/programs-working-groups/corporate-finance",
        "refreshed": today.isoformat(),
        "coverage_through": newest,
        "note": "Refreshed automatically by scripts/refresh_nber_cache.py from NBER's own program listing "
                "(the JSON feed behind the program page). data.nber.org blocks GitHub runners; this endpoint does not.",
        "papers": papers,
        "coverage_checked_through": today.isoformat(),
    }
    print(f"OK: {len(papers)} papers in cache ({len(new)} new), newest issue {newest}, program total {total}")
    if args.dry_run:
        print("dry run: cache not written")
        return 0
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
