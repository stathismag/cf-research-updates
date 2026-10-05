#!/usr/bin/env python3
"""Fast, network-free repository invariants checked before any upstream request."""
import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def fail(msg):
    print(f"STATIC AUDIT ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)

def text(path):
    return (ROOT/path).read_text(encoding="utf-8")

def main():
    cfg=json.loads(text(Path("scripts/config.json")))
    cache=json.loads(text(Path("cache/nber_cf_program.json")))
    sent=json.loads(text(Path("sent.json")))
    workflow=text(Path(".github/workflows/update.yml"))
    netlify=text(Path("netlify.toml"))
    index=text(Path("index.html"))
    build=text(Path("scripts/build_site.py"))
    fetch=text(Path("scripts/fetch_papers.py"))
    readme=text(Path("README.md"))

    # Required files / assets.
    for p in ("favicon.png","linkedin-preview.png","cache/nber_cf_program.json"):
        if not (ROOT/p).is_file():
            fail(f"missing required file: {p}")
    tomllib.loads(netlify)

    # Production safety.
    if re.search(r"(?m)^\s*schedule\s*:", workflow):
        fail("scheduled workflow must remain paused until manual validation passes")
    if "inputs.send_email == true && inputs.publish == true" not in workflow:
        fail("email is not gated by publish=true")
    if "git add data.json sent.json" in workflow:
        fail("sent.json must not be committed before a successful Buttondown send")
    if 'default: false' not in workflow:
        fail("manual safety defaults are missing")

    # Canonical SSRN design: Crossref only.
    ssrn=cfg.get("ssrn") or {}
    if ssrn.get("source")!="crossref_prefix" or ssrn.get("prefix")!="10.2139":
        fail(f"unexpected SSRN source config: {ssrn}")
    stale=("OpenAlex","Financial Economics Network","data.nber.org","linkedin-preview-v4.jpg")
    combined="\n".join((index,build,fetch,readme,netlify))
    found=[s for s in stale if s in combined]
    if found:
        fail(f"stale source/asset references remain: {found}")

    # NBER cache must be explicit, current, unique and parseable.
    if cache.get("program_code")!="CF":
        fail("NBER cache is not Corporate Finance (CF)")
    papers=cache.get("papers")
    if not isinstance(papers,list) or not papers:
        fail("NBER cache contains no papers")
    wps=[p.get("wp") for p in papers]
    if any(not re.fullmatch(r"w\d{4,6}", w or "") for w in wps):
        fail("NBER cache contains invalid working-paper IDs")
    if len(wps)!=len(set(wps)):
        fail("NBER cache contains duplicate working-paper IDs")
    for p in papers:
        try:
            dt.datetime.strptime(p["issue_month"],"%Y-%m")
        except Exception:
            fail(f"invalid NBER issue_month: {p}")
    refreshed=dt.date.fromisoformat(cache["refreshed"])
    age=(dt.date.today()-refreshed).days
    max_age=int((cfg.get("nber") or {}).get("cache_warn_days",7))
    if age<0 or age>max_age:
        fail(f"NBER cache age is {age} days; refresh it before running the update")

    # sent.json must retain insertion order and contain unique URLs.
    if not isinstance(sent,list) or len(sent)!=len(set(sent)):
        fail("sent.json must be a unique ordered list")

    # Social preview references must point at a real PNG.
    social="https://corporatefinanceupdates.com/linkedin-preview.png"
    if social not in index or social not in build:
        fail("canonical social preview PNG is not referenced by index/build")
    if 'from = "/linkedin-preview.png"' in netlify:
        fail("linkedin-preview.png must not redirect away from itself")
    if 'for = "/linkedin-preview.png"' not in netlify:
        fail("linkedin-preview.png Netlify header is missing")

    print(
        f"STATIC AUDIT PASS: {len(papers)} cached NBER CF papers; "
        f"cache age {age}d; schedule paused; publish/email safety intact"
    )
    return 0

if __name__=="__main__":
    raise SystemExit(main())
