#!/usr/bin/env python3
"""
Fetch new corporate-finance research from Crossref and write data.json.

  python scripts/fetch_papers.py            normal run (what the GitHub Action does)
  python scripts/fetch_papers.py --check    verify every ISSN in config.json
  python scripts/fetch_papers.py --days 30  override the look-back window
"""
import argparse
import csv
import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8"))
OUT = ROOT / "data.json"
API = "https://api.crossref.org"
CONTACT = os.environ.get("CROSSREF_EMAIL") or CFG["contact_email"]  # secret in the Action, config.json locally
HEADERS = {"User-Agent": f"cf-research-updates/1.0 (mailto:{CONTACT})"}
HTML_HEADERS = {**HEADERS, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.8"}
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


def date_from_parts(item, key):
    parts = ((item.get(key) or {}).get("date-parts") or [])
    if not parts or not parts[0]:
        return None
    y, *rest = parts[0]
    m = rest[0] if len(rest) > 0 else 1
    d = rest[1] if len(rest) > 1 else 1
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


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
    display_date = date_from_parts(item, "posted") if code == "SSRN" else None
    display_date = display_date or (item.get("created") or {}).get("date-time", "")[:10]
    return {
        "j": code, "t": title, "d": display_date,
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


def prefix_works(prefix, since, until, rows, max_pages):
    """Retrieve newly registered Crossref works under a DOI prefix.

    SSRN records are not reliably typed as journal-article, so querying the
    SSRN Electronic Journal ISSN with type:journal-article can return zero.
    SSRN DOIs use the 10.2139 prefix; query posted-content by its posted date.
    """
    cursor = "*"
    for _ in range(max_pages):
        data = get(f"{API}/prefixes/{prefix}/works", {
            "filter": f"from-posted-date:{since},until-posted-date:{until},type:posted-content",
            "rows": rows, "cursor": cursor,
            "select": "DOI,title,author,abstract,created,posted,URL,type",
        })
        if data is None:
            print(f"  WARNING: DOI prefix {prefix} not found on Crossref - check config.json", flush=True)
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


def _parse_ssrn_date(text):
    m = re.search(r"\bPosted\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\b", text or "", re.I)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%d %b %Y").date()
    except ValueError:
        return None


def _ssrn_entry_container(link):
    """Find the smallest useful result container around a title link."""
    node = link
    for _ in range(8):
        node = getattr(node, "parent", None)
        if node is None:
            break
        txt = node.get_text(" ", strip=True)
        if "Posted " in txt and len(txt) < 12000:
            return node
    return link.parent


def fetch_ssrn(since, until):
    """Fetch recent finance preprints directly from SSRN's Financial Economics Network."""
    cfg = CFG["ssrn"]
    since_d, until_d = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    base = "https://papers.ssrn.com/sol3/Jeljour_results.cfm"
    out, seen = [], set()
    raw_rows = 0

    for page in range(1, cfg.get("max_pages", 20) + 1):
        params = {
            "Network": "yes",
            "form_name": "journalBrowse",
            "journal_id": str(cfg.get("journal_id", 203)),
            "lim": "false",
            "orderBy": "ab_approval_date",
            "orderDir": "desc",
            "strSelectedOption": "6",
            "npage": str(page),
        }
        try:
            r = requests.get(base, params=params, headers=HEADERS, timeout=90)
            r.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"SSRN FEN request failed on page {page}: {e}") from e

        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select('a.title[href*="abstract="], a.title[href*="abstract_id="]')
        if not links:
            links = [
                a for a in soup.find_all("a", href=True)
                if re.search(r"(?:abstract=|abstract_id=)\d+", a.get("href", ""), re.I)
                and a.get_text(" ", strip=True)
            ]
        if not links:
            if page == 1:
                raise RuntimeError("SSRN FEN returned no paper rows; refusing to publish an empty SSRN feed.")
            break

        page_dates = []
        page_seen_ids = set()
        for link in links:
            href = link.get("href", "")
            mid = re.search(r"(?:abstract=|abstract_id=)(\d+)", href, re.I)
            if not mid:
                continue
            paper_id = mid.group(1)
            if paper_id in page_seen_ids:
                continue
            page_seen_ids.add(paper_id)

            container = _ssrn_entry_container(link)
            text = container.get_text(" ", strip=True)
            posted = _parse_ssrn_date(text)
            if not posted:
                continue
            raw_rows += 1
            page_dates.append(posted)

            if posted < since_d or posted > until_d:
                continue

            title = clean(link.get_text(" ", strip=True))
            if not title or SKIP_TITLE.search(title):
                continue

            if not SSRN_RX.search(title.lower()):
                continue

            url = urljoin("https://papers.ssrn.com", href)
            author_names = []
            for a in container.find_all("a", href=True):
                ah = a.get("href", "")
                name = clean(a.get_text(" ", strip=True))
                if name and ("AbsByAuth.cfm" in ah or "per_id=" in ah):
                    author_names.append(name)
            authors = ", ".join(dict.fromkeys(author_names)) or "—"

            themes, area = tag(title)
            doi = f"10.2139/ssrn.{paper_id}"
            if paper_id in seen:
                continue
            seen.add(paper_id)
            out.append({
                "j": "SSRN",
                "t": title,
                "d": posted.isoformat(),
                "a": area,
                "th": themes,
                "au": authors,
                "ab": "",
                "url": url,
                "doi": doi,
            })

        print(f"  SSRN FEN page {page:2d}: {len(page_dates):3d} dated rows", flush=True)

        if page_dates and min(page_dates) < since_d:
            break
        time.sleep(1)

    if raw_rows == 0:
        raise RuntimeError("SSRN FEN produced zero dated rows; refusing to treat this as a valid empty result.")

    out.sort(key=lambda p: (p["d"], p["t"]), reverse=True)
    print(f"  SSRN  {len(out):3d} kept of {raw_rows} dated FEN rows", flush=True)
    kept = out[: cfg.get("max_items", 200)]
    return kept, {"status": "ok", "raw_rows": raw_rows, "kept": len(kept)}


def _fetch_text(url, label, params=None):
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=HTML_HEADERS, timeout=90)
            if r.status_code == 200 and r.text.strip():
                return r.text
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = e
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"{label} request failed: {last}")


def _nber_issue_overlaps(value, since_d, until_d):
    value = clean(value)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m", "%m/%Y", "%B %Y", "%b %Y"):
        try:
            d = dt.datetime.strptime(value, fmt).date()
            if fmt in ("%Y-%m", "%m/%Y", "%B %Y", "%b %Y"):
                if d.month == 12:
                    month_end = dt.date(d.year + 1, 1, 1) - dt.timedelta(days=1)
                else:
                    month_end = dt.date(d.year, d.month + 1, 1) - dt.timedelta(days=1)
                return not (month_end < since_d or d > until_d)
            return since_d <= d <= until_d
        except ValueError:
            pass
    return False


def _nber_tsv(name):
    base = CFG["nber"]["metadata_base"].rstrip("/")
    text = _fetch_text(f"{base}/{name}.tsv", f"NBER {name}.tsv")
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    if not rows:
        raise RuntimeError(f"NBER {name}.tsv parsed to zero rows")
    return rows


def nber_candidates(since, until):
    """Return current-window candidates in the official NBER Corporate Finance program."""
    since_d, until_d = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    prog_rows = _nber_tsv("prog")
    ref_rows = _nber_tsv("ref")
    cf_ids = {
        clean(r.get("paper")).lower()
        for r in prog_rows
        if clean(r.get("program")).upper() == CFG["nber"].get("program_code", "CF")
    }
    if len(cf_ids) < CFG["nber"].get("min_program_ids", 100):
        raise RuntimeError(
            f"NBER program metadata has only {len(cf_ids)} Corporate Finance papers; refusing to publish."
        )

    candidates = []
    latest_issue = None
    for r in ref_rows:
        paper = clean(r.get("paper")).lower()
        if paper not in cf_ids or not paper.startswith("w"):
            continue
        issue = clean(r.get("issue_date"))
        # Track latest parsable issue month/date for freshness checks.
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m", "%m/%Y", "%B %Y", "%b %Y"):
            try:
                d = dt.datetime.strptime(issue, fmt).date()
                latest_issue = d if latest_issue is None or d > latest_issue else latest_issue
                break
            except ValueError:
                pass
        if _nber_issue_overlaps(issue, since_d, until_d):
            candidates.append(r)

    if latest_issue is None or latest_issue < until_d - dt.timedelta(days=120):
        raise RuntimeError(
            f"NBER metadata appears stale (latest issue date: {latest_issue}); refusing to publish."
        )
    return candidates, len(cf_ids), latest_issue


def fetch_nber(since, until):
    """Fetch current-window NBER Corporate Finance papers using official NBER metadata."""
    cfg = CFG["nber"]
    since_d, until_d = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    candidates, program_count, latest_issue = nber_candidates(since, until)
    out, checked, seen = [], 0, set()

    # Abstract metadata is official and avoids relying on Crossref for abstracts.
    abs_map = {
        clean(r.get("paper")).lower(): clean(r.get("abstract"))
        for r in _nber_tsv("abs")
    }

    for r in candidates:
        paper = clean(r.get("paper")).lower()
        doi = clean(r.get("doi")) or f"10.3386/{paper}"
        d = get(f"{API}/works/{doi}")
        if not d or not d.get("message"):
            continue
        checked += 1
        msg = d["message"]
        created = (msg.get("created") or {}).get("date-time", "")[:10]
        try:
            created_d = dt.date.fromisoformat(created)
        except ValueError:
            continue
        if not (since_d <= created_d <= until_d):
            continue

        title = clean(r.get("title")) or clean((msg.get("title") or [""])[0])
        if not title or SKIP_TITLE.search(title):
            continue
        authors = clean(r.get("author")) or "—"
        ab = abs_map.get(paper, "")
        limit = CFG.get("abstract_chars", 700)
        if len(ab) > limit:
            ab = ab[:limit].rsplit(" ", 1)[0] + "…"
        themes, area = tag(f"{title} {ab}")
        doi = doi.lower()
        if doi in seen:
            continue
        seen.add(doi)
        out.append({
            "j": "NBER",
            "t": title,
            "d": created_d.isoformat(),
            "a": area,
            "th": themes,
            "au": authors,
            "ab": ab,
            "url": f"https://www.nber.org/papers/{paper}",
            "doi": doi,
            "wp": paper[1:],
        })

    if candidates and checked == 0:
        raise RuntimeError("NBER current-window candidates found but none resolved in Crossref.")
    out.sort(key=lambda p: (p["d"], p["t"]), reverse=True)
    print(
        f"  NBER  {len(out):3d} kept from {len(candidates)} current-month CF candidates "
        f"({program_count} total CF papers)",
        flush=True,
    )
    return out[: cfg.get("max_items", 100)], {
        "status": "ok",
        "program_ids": program_count,
        "metadata_checked": checked,
        "candidates": len(candidates),
        "kept": len(out),
        "latest_seen": latest_issue.isoformat(),
    }

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
    baseline = today - dt.timedelta(days=CFG.get("window_days", 14))
    if prev:
        # Preserve continuity with the previous issue, but never let a manual
        # rerun shrink the visible window below the normal bi-weekly span.
        linked = dt.date.fromisoformat(prev) - dt.timedelta(days=CFG.get("overlap_days", 2))
        since = min(baseline, linked)
    else:
        since = baseline
    since = max(since, today - dt.timedelta(days=CFG.get("max_window_days", 45)))
    return since, today


def check():
    for code, j in CFG["journals"].items():
        d = get(f"{API}/journals/{j['issn']}")
        name = d["message"]["title"] if d else "NOT FOUND - fix this ISSN"
        if not d:
            raise RuntimeError(f"{code} ISSN {j['issn']} not found in Crossref")
        print(f"{code:5s} {j['issn']:10s} {name}")

    ssrn = CFG["ssrn"]
    base = "https://papers.ssrn.com/sol3/Jeljour_results.cfm"
    html_text = _fetch_text(
        base,
        "SSRN FEN",
        params={
            "Network": "yes", "form_name": "journalBrowse",
            "journal_id": str(ssrn.get("journal_id", 203)),
            "lim": "false", "orderBy": "ab_approval_date",
            "orderDir": "desc", "strSelectedOption": "6", "npage": "1",
        },
    )
    soup = BeautifulSoup(html_text, "html.parser")
    ssrn_links = [
        a for a in soup.find_all("a", href=True)
        if re.search(r"(?:abstract=|abstract_id=)\d+", a.get("href", ""), re.I)
    ]
    if not ssrn_links:
        raise RuntimeError("SSRN FEN preflight found no paper links")
    print(f"SSRN  FEN preflight: {len(ssrn_links)} paper links")

    today = dt.date.today()
    candidates, total_cf, latest_issue = nber_candidates(
        (today - dt.timedelta(days=31)).isoformat(), today.isoformat()
    )
    print(
        f"NBER  official metadata preflight: {total_cf} CF papers, "
        f"{len(candidates)} recent candidates, latest issue {latest_issue}"
    )

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
    if CFG["ssrn"].get("enabled", True):
        ssrn, ssrn_health = fetch_ssrn(since.isoformat(), until.isoformat())
    else:
        ssrn, ssrn_health = [], {"status": "disabled", "raw_rows": 0, "kept": 0}
    if CFG.get("nber", {}).get("enabled", True):
        nber, nber_health = fetch_nber(since.isoformat(), until.isoformat())
    else:
        nber, nber_health = [], {"status": "disabled", "program_ids": 0, "metadata_checked": 0, "kept": 0}
    pubs.sort(key=lambda p: (p["d"], p["j"]), reverse=True)
    data = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "window": {"from": since.isoformat(), "to": until.isoformat()},
        "repo": CFG.get("repo_url", ""),
        "journals": {k: v["name"] for k, v in CFG["journals"].items()},
        "themes": {k: {"n": v["name"], "d": v["description"], "c": v["color"]} for k, v in CFG["themes"].items()},
        "areas": list(CFG["areas"].keys()),
        "pubs": pubs,
        "nber": nber,
        "ssrn": ssrn,
        "source_health": {"nber": nber_health, "ssrn": ssrn_health},
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {OUT.name}: {len(pubs)} articles, {len(nber)} NBER papers, {len(ssrn)} SSRN papers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
