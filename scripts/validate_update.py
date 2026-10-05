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

    grace=dt.timedelta(days=int((CFG.get("nber") or {}).get("catchup_days",21)))
    for key in ("pubs","nber","ssrn"):
        rows=d.get(key)
        if not isinstance(rows,list): fail(f"{key} is not a list")
        lo=start-grace if key=="nber" else start
        seen=set()
        for p in rows:
            validate_paper(p,key,lo,end)
            ident=p.get("doi") or p.get("url")
            if ident in seen: fail(f"{key}: duplicate {ident}")
            seen.add(ident)

    if not d["pubs"]: fail("journal feed unexpectedly empty")

    health=d.get("source_health") or {}
    sh=health.get("ssrn") or {}
    nh=health.get("nber") or {}
    expected_ssrn_source=f"Crossref DOI prefix {CFG['ssrn']['prefix']}"
    if sh.get("status") not in {"ok","degraded"}:
        fail(f"SSRN source unhealthy: {sh}")
    if sh.get("source")!=expected_ssrn_source:
        fail(f"Unexpected SSRN source: {sh}; expected {expected_ssrn_source}")
    if sh.get("status")=="ok":
        if int(sh.get("raw_rows",0))<=0 or sh.get("complete") is not True:
            fail(f"SSRN pagination incomplete: {sh}")
        if int(sh.get("dropped_by_cap",0))>0:
            print(f"WARNING: SSRN cap dropped {sh['dropped_by_cap']} of {sh.get('keyword_matches')} matches", file=sys.stderr)
    else:
        print(f"WARNING: SSRN source degraded; last-known-good rows retained: {sh}", file=sys.stderr)
    if int(sh.get("kept",-1)) != len(d["ssrn"]):
        fail(f"SSRN health/data count mismatch: {sh} vs {len(d['ssrn'])}")
    if nh.get("status") not in {"ok","degraded"}:
        fail(f"NBER source unhealthy: {nh}")
    if nh.get("status")=="ok":
        candidates=int(nh.get("candidates",0))
        checked=int(nh.get("metadata_checked",0))
        unresolved=int(nh.get("unresolved",0))
        if candidates < 0 or checked < 0 or unresolved < 0 or checked + unresolved != candidates:
            fail(f"NBER source counts inconsistent: {nh}")
    else:
        print(f"WARNING: NBER source degraded; last-known-good rows retained: {nh}", file=sys.stderr)
    if int(nh.get("kept",-1)) != len(d["nber"]):
        fail(f"NBER health/data count mismatch: {nh} vs {len(d['nber'])}")

    ssrn_titles=set()
    for p in d["ssrn"]:
        k=re.sub(r"[^a-z0-9]+","",(p.get("t") or "").lower())
        if k in ssrn_titles: fail(f"duplicate normalized SSRN title: {p.get('t')}")
        ssrn_titles.add(k)
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
