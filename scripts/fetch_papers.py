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
    r"call for papers|list of reviewers|acknowledg|american finance association$)", re.I)
SKIP_TITLE_ANY = re.compile(r"\b(corrigendum|erratum|retraction)\b", re.I)
_HTML_TAG = re.compile(r"</?(?:br|p|a|i|b|u|em|strong|sub|sup|span|div)\b[^>]*>", re.I)
_NBER_NOTE = re.compile(r"Institutional subscribers to the NBER working paper series.*$", re.I | re.S)
NBER_CACHE = ROOT / "cache" / "nber_cf_program.json"


def rx(words):
    """Word-boundary regex from a keyword list; a trailing * matches any suffix."""
    parts = []
    for w in words:
        w = w.lower()
        parts.append(re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w))
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")


THEME_RX = {k: rx(v["keywords"]) for k, v in CFG["themes"].items()}
AREA_RX = {k: rx(v) for k, v in CFG["areas"].items()}
SSRN_CONTEXT_RX = rx(["firm*", "corporate", "company", "companies", "CEO*", "board*", "shareholder*"])


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
    s = html.unescape(html.unescape(s))
    s = _HTML_TAG.sub(" ", s)
    s = _NBER_NOTE.sub("", s)
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
    if not title or SKIP_TITLE.search(title) or SKIP_TITLE_ANY.search(title):
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
    display_date = (item.get("created") or {}).get("date-time", "")[:10]
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


def ssrn_score(p):
    """Ranking only: title hits count double; corporate context adds two."""
    title = p["t"].lower()
    text = f"{title} {p.get('ab','').lower()}"
    return (
        2 * sum(bool(r.search(title)) for r in AREA_RX.values())
        + sum(bool(r.search(text)) for r in AREA_RX.values())
        + 2 * bool(SSRN_CONTEXT_RX.search(text))
    )


def fetch_ssrn(since, until):
    """SSRN DOI registrations (Crossref prefix 10.2139) created in the window."""
    cfg = CFG["ssrn"]
    out, seen = [], set()
    cursor, raw_rows, total_available, exhausted = "*", 0, None, False
    rows, max_pages = 1000, cfg.get("max_pages", 60)

    for _ in range(max_pages):
        data = get(f"{API}/prefixes/{cfg['prefix']}/works", {
            "filter": f"from-created-date:{since},until-created-date:{until}",
            "rows": rows, "cursor": cursor,
            "select": "DOI,title,author,abstract,created,URL",
        })
        if data is None:
            raise RuntimeError(f"Crossref SSRN prefix {cfg['prefix']} was not found")
        msg = data.get("message") or {}
        if total_available is None:
            total_available = int(msg.get("total-results") or 0)

        items = msg.get("items") or []
        raw_rows += len(items)
        for it in items:
            p = parse(it, "SSRN")
            if not p or p["doi"] in seen:
                continue
            text = f"{p['t']} {p.get('ab','')}".lower()
            classic = p["a"] != "General"
            themed = bool(p["th"]) and bool(SSRN_CONTEXT_RX.search(text))
            if not (classic or themed):
                continue
            seen.add(p["doi"])
            out.append(p)

        cursor = msg.get("next-cursor")
        if len(items) < rows or not cursor:
            exhausted = True
            break

    total_available = total_available or 0
    drift = total_available - raw_rows
    complete = exhausted and raw_rows > 0 and drift <= max(5, total_available // 200)
    if not complete:
        raise RuntimeError(
            f"Crossref SSRN retrieval incomplete: {raw_rows} of {total_available} records, "
            f"cursor exhausted={exhausted}. Raise ssrn.max_pages if the window is long."
        )

    out.sort(key=lambda p: (ssrn_score(p), p["d"], p["t"]), reverse=True)
    kept = out[:cfg.get("max_items", 300)]
    kept.sort(key=lambda p: (p["d"], p["t"]), reverse=True)
    print(
        f"  SSRN examined {raw_rows} of {total_available}, matched {len(out)}, "
        f"kept {len(kept)}, dropped by cap {len(out)-len(kept)}",
        flush=True,
    )
    return kept, {
        "status": "ok",
        "source": f"Crossref DOI prefix {cfg['prefix']}",
        "total_available": total_available,
        "raw_rows": raw_rows,
        "complete": True,
        "keyword_matches": len(out),
        "kept": len(kept),
        "dropped_by_cap": len(out) - len(kept),
    }



def _load_nber_cache():
    if not NBER_CACHE.exists():
        raise RuntimeError(f"Missing NBER cache: {NBER_CACHE}")
    data = json.loads(NBER_CACHE.read_text(encoding="utf-8"))
    if data.get("program_code") != CFG["nber"].get("program_code", "CF"):
        raise RuntimeError("NBER cache program code does not match config")
    papers = data.get("papers")
    if not isinstance(papers, list) or not papers:
        raise RuntimeError("NBER cache contains no Corporate Finance papers")
    refreshed = dt.date.fromisoformat(data["refreshed"])
    age = (dt.date.today() - refreshed).days
    return data, papers, age


def _issue_month(value):
    try:
        d = dt.datetime.strptime(value, "%Y-%m").date()
        return d.year, d.month
    except (TypeError, ValueError):
        return None


def published_nber_dois(exclude):
    """DOIs of NBER papers already shown in earlier issue snapshots."""
    dois = set()
    for f in (ROOT / "issues").glob("*/data.json"):
        if f.parent.name == exclude:
            continue
        try:
            rows = json.loads(f.read_text(encoding="utf-8")).get("nber", [])
        except Exception:
            continue
        dois.update((p.get("doi") or "").lower() for p in rows)
    return {d for d in dois if d}


def nber_candidates(since, until):
    """Current-window candidates from the versioned NBER Corporate Finance cache."""
    since_d, until_d = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    cache, papers, age = _load_nber_cache()
    first, last = (since_d.year, since_d.month), (until_d.year, until_d.month)
    candidates = [p for p in papers if (_issue_month(p.get("issue_month")) or (0,0)) >= first
                  and (_issue_month(p.get("issue_month")) or (9999,12)) <= last]
    return candidates, cache, age


def fetch_nber(since, until):
    """Fetch cached NBER Corporate Finance membership; resolve metadata via Crossref."""
    cfg = CFG["nber"]
    since_d, until_d = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    catchup_d = since_d - dt.timedelta(days=cfg.get("catchup_days", 21))
    candidates, cache, cache_age = nber_candidates(catchup_d.isoformat(), until)
    already = published_nber_dois(exclude=until)
    out, checked, unresolved, caught_up, seen = [], 0, 0, 0, set()

    for r in candidates:
        paper = clean(r.get("wp")).lower()
        if not re.fullmatch(r"w\d{4,6}", paper):
            continue
        doi = f"10.3386/{paper}"
        d = get(f"{API}/works/{doi}")
        if not d or not d.get("message"):
            unresolved += 1
            continue
        checked += 1
        msg = d["message"]
        created = (msg.get("created") or {}).get("date-time", "")[:10]
        try:
            created_d = dt.date.fromisoformat(created)
        except ValueError:
            unresolved += 1
            continue

        in_window = since_d <= created_d <= until_d
        late = catchup_d <= created_d < since_d and doi not in already
        if not (in_window or late):
            continue
        caught_up += int(late)

        title = clean(r.get("title")) or clean((msg.get("title") or [""])[0])
        if not title or SKIP_TITLE.search(title) or SKIP_TITLE_ANY.search(title):
            continue
        authors = clean(r.get("authors"))
        if not authors:
            authors = ", ".join(
                " ".join(x for x in (a.get("given"), a.get("family")) if x)
                for a in msg.get("author", [])
                if a.get("given") or a.get("family")
            ) or "—"
        ab = clean(msg.get("abstract", ""))
        limit = CFG.get("abstract_chars", 700)
        if len(ab) > limit:
            ab = ab[:limit].rsplit(" ", 1)[0] + "…"
        themes, area = tag(f"{title} {ab}")
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

    out.sort(key=lambda p: (p["d"], p["t"]), reverse=True)
    cache_warn = cfg.get("cache_warn_days", 45)
    status = "ok" if cache_age <= cache_warn and (checked > 0 or not candidates) else "degraded"
    print(
        f"  NBER {len(out):3d} kept from {len(candidates)} cached CF candidates; "
        f"resolved {checked}, unresolved {unresolved}, catch-up {caught_up}, cache age {cache_age}d",
        flush=True,
    )
    return out[:cfg.get("max_items", 100)], {
        "status": status,
        "source": "repo NBER CF cache + Crossref",
        "cache_refreshed": cache.get("refreshed"),
        "cache_age_days": cache_age,
        "candidates": len(candidates),
        "metadata_checked": checked,
        "unresolved": unresolved,
        "caught_up": caught_up,
        "kept": len(out),
    }



def title_key(title):
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())



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
    d = get(f"{API}/prefixes/{ssrn['prefix']}")
    if not d or not d.get("message"):
        raise RuntimeError(f"Crossref SSRN prefix {ssrn['prefix']} not found")
    owner = (d.get("message") or {}).get("name") or "registered prefix"
    print(f"SSRN  Crossref prefix {ssrn['prefix']}: {owner}")

    cache, papers, age = _load_nber_cache()
    print(
        f"NBER  cached CF membership: {len(papers)} papers, refreshed {cache.get('refreshed')}, "
        f"age {age} days"
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

    nber_titles = {title_key(p.get("t")) for p in nber if p.get("t")}
    before = len(ssrn)
    ssrn = [p for p in ssrn if title_key(p.get("t")) not in nber_titles]
    ssrn_health["deduped_against_nber"] = before - len(ssrn)
    ssrn_health["kept"] = len(ssrn)

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
