#!/usr/bin/env python3
"""Build crawlable HTML, permanent issue snapshots, archive, sitemap and robots.txt."""
from __future__ import annotations
import html, json, re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data.json"; INDEX=ROOT/"index.html"; ISSUES=ROOT/"issues"; ARCHIVE=ROOT/"archive"
SITE_URL="https://corporatefinanceupdates.com"
SOCIAL_IMAGE="https://corporatefinanceupdates.com/linkedin-preview.jpg?v=20261003-2"

def esc(v): return html.escape(str(v or ""), quote=True)
def period(d):
    w=d.get("window") or {}
    return f"{w.get('from','—')} to {w.get('to','—')}"

def render_stats(d):
    vals=[(len(d.get("pubs",[])),"Journal articles"),(len(d.get("ssrn",[])),"SSRN papers"),(0,"Conferences"),(0,"Job openings")]
    return "".join(f'<div class="stat"><b>{n}</b><span>{esc(label)}</span></div>' for n,label in vals)

def render_radar(d):
    papers=[*d.get("pubs",[]),*d.get("ssrn",[])]; themes=d.get("themes",{})
    counts={k:sum(k in (p.get("th") or []) for p in papers) for k in themes}; maximum=max([1,*counts.values()])
    out=[]
    for k,t in themes.items():
        n=counts[k]
        out.append(f'<div class="tc" style="--c:{esc(t.get("c"))}" data-theme="{esc(k)}"><div class="th"><b>{esc(t.get("n"))}</b><span class="cnt">{n}</span></div><div class="td">{esc(t.get("d"))}</div><div class="bar"><i style="width:{n/maximum*100}%"></i></div></div>')
    return "".join(out)

def render_cards(d,papers,expand=True):
    themes=d.get("themes",{}); journals=d.get("journals",{}); out=[]
    for p in papers:
        tags="".join(f'<span class="tg t" style="--c:{esc(themes[k].get("c"))}">{esc(themes[k].get("n"))}</span>' for k in (p.get("th") or []) if k in themes)
        ab=esc(p.get("ab")) or "<i>No abstract available.</i>"; style=' style="display:block"' if expand else ""
        out.append(f'<article class="item"><div class="row1"><span class="jb" title="{esc(journals.get(p.get("j"),"SSRN"))}">{esc(p.get("j"))}</span><span class="ti">{esc(p.get("t"))}</span><span class="dt">{esc(p.get("d"))}</span></div><div class="au">{esc(p.get("au"))}</div><div class="tags"><span class="tg">{esc(p.get("a"))}</span>{tags}</div><div class="ab"{style}>{ab} <a href="{esc(p.get("url"))}" target="_blank" rel="noopener">Open paper →</a></div></article>')
    return "".join(out)

def render_coverage(d):
    journals=", ".join(f"{esc(v)} ({esc(k)})" for k,v in d.get("journals",{}).items()) or "—"
    themes=", ".join(esc(v.get("n")) for v in d.get("themes",{}).values()) or "—"
    areas=", ".join(esc(a) for a in d.get("areas",[])) or "—"
    return f'<p><b>Journals included:</b> {journals}. No guarantee of completeness.</p><p style="margin-top:10px"><b>SSRN:</b> new working papers whose DOI was registered by SSRN in the period and whose title matches corporate-finance keywords. Abstracts are usually unavailable for SSRN entries, so treat the SSRN list as approximate.</p><p style="margin-top:10px"><b>Theme tagging:</b> each paper is matched against keyword dictionaries for {themes}, and assigned one classic area ({areas}).</p><p style="margin-top:10px"><b>Conferences &amp; jobs:</b> submitted by the community and reviewed before publication.</p><p style="margin-top:10px"><b>Last paper update:</b> {esc(d.get("generated") or "never")}.</p>'

def replace_block(text,name,content):
    s=f"<!-- STATIC_{name}_START -->"; e=f"<!-- STATIC_{name}_END -->"; pat=re.compile(re.escape(s)+r".*?"+re.escape(e),re.S)
    if not pat.search(text): raise RuntimeError(f"Missing static marker block: {name}")
    return pat.sub(lambda _m:s+content+e,text,count=1)

def build_root(d):
    t=INDEX.read_text(encoding="utf-8")
    t=replace_block(t,"STATS",render_stats(d)); t=replace_block(t,"PERIOD",f"Share of new papers per theme, {esc(period(d))}. Click a theme to filter.")
    t=replace_block(t,"RADAR",render_radar(d)); t=replace_block(t,"PANEL",f'<div class="meta">{len(d.get("pubs",[]))} online-first articles · {esc(period(d))}</div>'+render_cards(d,d.get("pubs",[]),True))
    t=replace_block(t,"COVERAGE",render_coverage(d)); INDEX.write_text(t,encoding="utf-8")

def issue_page(d,date):
    pubs=d.get("pubs",[]); ssrn=d.get("ssrn",[])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Corporate Finance Research Updates — {esc(date)}</title><meta name="description" content="Permanent corporate-finance research digest for {esc(period(d))}."><meta name="robots" content="index,follow"><link rel="canonical" href="{SITE_URL}/issues/{esc(date)}/"><meta property="og:type" content="article"><meta property="og:title" content="Corporate Finance Research Updates — {esc(date)}"><meta property="og:description" content="Permanent corporate-finance research digest for {esc(period(d))}."><meta property="og:url" content="{SITE_URL}/issues/{esc(date)}/"><meta property="og:image" content="{SOCIAL_IMAGE}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="627"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{SOCIAL_IMAGE}"><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:#f7f6f2;color:#0f1b2d;line-height:1.5;margin:0}}main{{max-width:1050px;margin:auto;padding:34px 22px}}a{{color:#0e7c66}}.item{{background:#fff;border:1px solid #e3e7ee;border-radius:10px;padding:14px 16px;margin:10px 0}}.row1{{display:flex;gap:10px;flex-wrap:wrap}}.jb{{font-size:11px;font-weight:700;background:#eef3f8;padding:3px 7px;border-radius:5px}}.ti{{font-weight:650;flex:1;min-width:260px}}.dt,.au,.meta{{font-size:13px;color:#6b7a90}}.tags{{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}}.tg{{font-size:11.5px;padding:2px 8px;border-radius:12px;border:1px solid #e3e7ee}}.tg.t{{color:#fff;background:var(--c);border:0}}.ab{{font-size:13.5px;margin-top:10px;padding-top:10px;border-top:1px dashed #e3e7ee}}</style></head><body><main><a href="/">← Latest</a> · <a href="/archive/">Archive</a><h1>Corporate Finance Research Updates — {esc(date)}</h1><p class="meta">Coverage: {esc(period(d))} · {len(pubs)} journal articles · {len(ssrn)} SSRN papers · generated {esc(d.get("generated"))}</p><h2>Journal publications</h2>{render_cards(d,pubs,True) or "<p>No journal publications in this issue.</p>"}<h2>SSRN working papers</h2>{render_cards(d,ssrn,True) or "<p>No SSRN papers in this issue.</p>"}</main></body></html>'''

def rows():
    out=[]
    if ISSUES.exists():
        for f in ISSUES.iterdir():
            p=f/"data.json"
            if f.is_dir() and p.exists():
                try: out.append((f.name,json.loads(p.read_text(encoding="utf-8"))))
                except Exception: pass
    return sorted(out,key=lambda x:x[0],reverse=True)

def build_archive(items):
    ARCHIVE.mkdir(exist_ok=True)
    lis="".join(f'<li><a href="/issues/{esc(date)}/"><b>{esc(date)}</b></a> — {esc(period(d))} · {len(d.get("pubs",[]))} journal articles · {len(d.get("ssrn",[]))} SSRN papers</li>' for date,d in items) or "<li>No archived issues yet.</li>"
    page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Archive — Corporate Finance Research Updates</title><meta name="description" content="Permanent archive of Corporate Finance Research Updates issues."><meta name="robots" content="index,follow"><link rel="canonical" href="{SITE_URL}/archive/"><meta property="og:type" content="website"><meta property="og:title" content="Archive — Corporate Finance Research Updates"><meta property="og:description" content="Permanent archive of Corporate Finance Research Updates issues."><meta property="og:url" content="{SITE_URL}/archive/"><meta property="og:image" content="{SOCIAL_IMAGE}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="627"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{SOCIAL_IMAGE}"><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:#f7f6f2;color:#0f1b2d}}main{{max-width:850px;margin:auto;padding:40px 22px}}a{{color:#0e7c66}}li{{background:#fff;border:1px solid #e3e7ee;border-radius:9px;padding:14px;margin:10px 0;list-style:none}}ul{{padding:0}}</style></head><body><main><a href="/">← Latest</a><h1>Issue archive</h1><p>Permanent snapshots of each research update.</p><ul>{lis}</ul></main></body></html>'''
    (ARCHIVE/"index.html").write_text(page,encoding="utf-8")

def discovery(items):
    urls=[f"{SITE_URL}/",f"{SITE_URL}/archive/"]+[f"{SITE_URL}/issues/{date}/" for date,_ in items]
    sm='<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/1.0">\n'+"".join(f"  <url><loc>{html.escape(u)}</loc></url>\n" for u in urls)+"</urlset>\n"
    (ROOT/"sitemap.xml").write_text(sm,encoding="utf-8"); (ROOT/"robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n",encoding="utf-8")

def main():
    d=json.loads(DATA.read_text(encoding="utf-8")); date=(d.get("window") or {}).get("to")
    if not date: raise RuntimeError("data.json has no window.to")
    folder=ISSUES/date; folder.mkdir(parents=True,exist_ok=True)
    (folder/"data.json").write_text(json.dumps(d,ensure_ascii=False,indent=1)+"\n",encoding="utf-8"); (folder/"index.html").write_text(issue_page(d,date),encoding="utf-8")
    build_root(d); items=rows(); build_archive(items); discovery(items)
    print(f"Built crawlable root, issue {date}, archive ({len(items)} issue(s)), sitemap and robots.txt")

if __name__=="__main__": main()
