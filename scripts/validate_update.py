#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data.json"
CFG=json.loads((ROOT/"scripts"/"config.json").read_text(encoding="utf-8"))
REQUIRED={"j","t","d","a","th","au","ab","url","doi"}

def fail(msg):
    print(f"VALIDATION ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)

def validate_paper(p, source, start, end):
    missing=REQUIRED-set(p)
    if missing: fail(f"{source}: missing fields {sorted(missing)}")
    if not p["t"] or not isinstance(p["th"], list): fail(f"{source}: invalid title/themes")
    try: day=dt.date.fromisoformat(p["d"])
    except Exception: fail(f"{source}: invalid date {p.get('d')!r}")
    if not (start <= day <= end): fail(f"{source}: out-of-window paper {p['d']} {p['t'][:80]}")
    u=urlparse(p["url"])
    if u.scheme not in {"http","https"} or not u.netloc: fail(f"{source}: invalid URL {p['url']}")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--site", action="store_true")
    args=ap.parse_args()
    d=json.loads(DATA.read_text(encoding="utf-8"))
    w=d.get("window") or {}
    try:
        start,end=dt.date.fromisoformat(w["from"]),dt.date.fromisoformat(w["to"])
    except Exception: fail("invalid window")
    if start>end: fail("window start after end")

    for key in ("pubs","nber","ssrn"):
        rows=d.get(key)
        if not isinstance(rows,list): fail(f"{key} is not a list")
        seen=set()
        for p in rows:
            validate_paper(p,key,start,end)
            ident=p.get("doi") or p.get("url")
            if ident in seen: fail(f"{key}: duplicate {ident}")
            seen.add(ident)

    if not d["pubs"]: fail("journal feed unexpectedly empty")

    health=d.get("source_health") or {}
    sh=health.get("ssrn") or {}
    nh=health.get("nber") or {}
    if sh.get("status")!="ok" or int(sh.get("raw_rows",0))<=0:
        fail(f"SSRN source unhealthy: {sh}")
    if sh.get("source")!="OpenAlex repository":
        fail(f"Unexpected SSRN source: {sh}")
    if int(sh.get("raw_rows",0)) != int(sh.get("total_available",-1)):
        fail(f"SSRN pagination incomplete: {sh}")
    minimum=int((CFG.get("nber") or {}).get("min_program_ids",10))
    if nh.get("status")!="ok" or int(nh.get("program_ids",0))<minimum:
        fail(f"NBER source unhealthy: {nh}")
    candidates=int(nh.get("candidates",0))
    checked=int(nh.get("metadata_checked",0))
    if candidates < 0 or checked < 0 or checked > candidates:
        fail(f"NBER source counts inconsistent: {nh}")
    if candidates > 0 and checked != candidates:
        fail(f"NBER metadata did not resolve every current-window candidate: {nh}")

    if len(d["ssrn"])>int(CFG["ssrn"].get("max_items",200)): fail("SSRN cap exceeded")
    if len(d["nber"])>int(CFG["nber"].get("max_items",100)): fail("NBER cap exceeded")

    if args.site:
        index=(ROOT/"index.html").read_text(encoding="utf-8")
        for token in ('["nber","NBER"', "NBER Corporate Finance", "D.nber"):
            if token not in index: fail(f"index.html missing {token}")
        latest=w["to"]
        issue=ROOT/"issues"/latest/"index.html"
        if not issue.exists(): fail(f"missing issue page {issue}")
        txt=issue.read_text(encoding="utf-8")
        if "NBER Working Papers" not in txt or "SSRN Working Papers" not in txt:
            fail("issue page missing working-paper sections")
        archive=(ROOT/"archive"/"index.html")
        if not archive.exists() or "NBER papers" not in archive.read_text(encoding="utf-8"):
            fail("archive missing NBER counts")

    print(f"VALIDATION PASS: {len(d['pubs'])} journals, {len(d['nber'])} NBER, {len(d['ssrn'])} SSRN")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
