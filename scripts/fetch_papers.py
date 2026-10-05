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


def _fetch_html(url, params=None, label="HTML source"):
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=HTML_HEADERS, timeout=90)
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = e
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"{label} request failed: {last}")


def nber_program_ids():
    """Return current NBER Corporate Finance Program working-paper IDs."""
    cfg = CFG["nber"]
    html_text = _fetch_html(
        cfg["program_url"],
        params={"perPage": cfg.get("per_page", 100)},
        label="NBER Corporate Finance Program",
    )
    soup = BeautifulSoup(html_text, "html.parser")
    ids = []
    for a in soup.find_all("a", href=True):
        m = re.search(r"/papers/(w\d+)(?:$|[?#])", a.get("href", ""), re.I)
        if m:
            wp = m.group(1).lower()
            if wp not in ids:
                ids.append(wp)
    minimum = cfg.get("min_program_ids", 10)
    if len(ids) < minimum:
        raise RuntimeError(
            f"NBER Corporate Finance Program parser found only {len(ids)} paper IDs "
            f"(minimum expected {minimum}); refusing to publish."
        )
    return ids


def fetch_nber(since, until):
    """Fetch NBER Corporate Finance Program papers registered in the update window."""
    cfg = CFG["nber"]
    since_d, until_d = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    ids = nber_program_ids()
    out, checked = [], 0
    seen = set()
    latest_seen = None

    for wp in ids:
        d = get(f"{API}/works/10.3386/{wp}")
        if not d or not d.get("message"):
            continue
        checked += 1
        p = parse(d["message"], "NBER")
        if not p:
            continue
        try:
            pdate = dt.date.fromisoformat(p["d"])
        except (TypeError, ValueError):
            continue
        latest_seen = pdate if latest_seen is None or pdate > latest_seen else latest_seen
        if not (since_d <= pdate <= until_d):
            continue
        if p["doi"] in seen:
            continue
        seen.add(p["doi"])
        p["url"] = f"https://www.nber.org/papers/{wp}"
        p["wp"] = wp[1:]
        out.append(p)

    if checked < min(10, cfg.get("min_program_ids", 10)):
        raise RuntimeError(
            f"NBER metadata check succeeded for only {checked} program papers; refusing to publish."
        )
    if latest_seen is None or latest_seen < until_d - dt.timedelta(days=90):
        raise RuntimeError(
            f"NBER program metadata appears stale (latest Crossref registration: {latest_seen}); "
            "refusing to publish."
        )

    out.sort(key=lambda p: (p["d"], p["t"]), reverse=True)
    print(f"  NBER  {len(out):3d} recent Corporate Finance papers from {len(ids)} program IDs", flush=True)
    return out[: cfg.get("max_items", 100)], {
        "status": "ok",
        "program_ids": len(ids),
        "metadata_checked": checked,
        "kept": len(out),
        "latest_seen": latest_seen.isoformat() if latest_seen else None,
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
    html_text = _fetch_html(
        base,
        params={
            "Network": "yes", "form_name": "journalBrowse",
            "journal_id": str(ssrn.get("journal_id", 203)),
            "lim": "false", "orderBy": "ab_approval_date",
            "orderDir": "desc", "strSelectedOption": "6", "npage": "1",
        },
        label="SSRN FEN",
    )
    soup = BeautifulSoup(html_text, "html.parser")
    ssrn_links = [
        a for a in soup.find_all("a", href=True)
        if re.search(r"(?:abstract=|abstract_id=)\d+", a.get("href", ""), re.I)
    ]
    if not ssrn_links:
        raise RuntimeError("SSRN FEN preflight found no paper links")
    print(f"SSRN  FEN preflight: {len(ssrn_links)} paper links")

    ids = nber_program_ids()
    print(f"NBER  Corporate Finance Program preflight: {len(ids)} working-paper IDs")

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
