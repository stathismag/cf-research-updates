#!/usr/bin/env python3
"""
Email the new papers in data.json to the Buttondown newsletter.

  python scripts/send_digest.py             send (needs BUTTONDOWN_API_KEY)
  python scripts/send_digest.py --dry-run   print the email, send nothing
  python scripts/send_digest.py --draft     save as a draft in Buttondown instead of sending

Papers already emailed are remembered in sent.json, so reruns never repeat a paper.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
SENT = ROOT / "sent.json"
API = os.environ.get("BUTTONDOWN_API_URL", "https://api.buttondown.com/v1/emails")
SITE = os.environ.get("SITE_URL", "https://cf-research-updates.netlify.app")
SENT_CAP = 3000


def name_case(s):
    """Crossref sometimes returns ALL-CAPS author names."""
    return s.title() if s and s.isupper() else s


def clip(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def entry(p, journals, abstract=False):
    title = p["t"].replace("[", "(").replace("]", ")")
    link = f"[{title}]({p['url']})" if p.get("url") else title
    meta = ", ".join(x for x in [name_case(p.get("au", "")), journals.get(p["j"], p["j"]), p.get("d", "")] if x)
    out = f"- **{link}**  \n  {meta}"
    if abstract and p.get("ab"):
        out += f"  \n  {clip(p['ab'], 230)}"
    return out


def build(data, papers):
    journals, themes = data["journals"], data["themes"]
    w = data.get("window") or {}
    subject = f"Corporate Finance Research Updates: {w.get('from', '')} to {w.get('to', '')}".strip(": ")
    parts = [f"{len(papers)} new papers from leading finance journals, tagged by theme and area.\n"]
    shown = set()
    for key, t in themes.items():
        rows = [p for p in papers if key in p.get("th", [])]
        if rows:
            parts.append(f"## {t['n']}\n\n" + "\n\n".join(entry(p, journals, True) for p in rows))
            shown.update(id(p) for p in rows)
    rest = [p for p in papers if id(p) not in shown]
    for area in data.get("areas", []):
        rows = [p for p in rest if p.get("a") == area]
        if rows:
            parts.append(f"## {area}\n\n" + "\n\n".join(entry(p, journals) for p in rows))
    other = [p for p in rest if p.get("a") not in data.get("areas", [])]
    if other:
        parts.append("## Other\n\n" + "\n\n".join(entry(p, journals) for p in other))
    parts.append(f"---\n\nBrowse, filter and search everything at [{SITE.split('//')[-1]}]({SITE}).\n\nCurated by [Efstathios Magerakis](https://smagerakis.gr).")
    return subject, "\n\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--draft", action="store_true")
    args = ap.parse_args()

    data = json.loads(DATA.read_text(encoding="utf-8"))
    sent = set(json.loads(SENT.read_text(encoding="utf-8"))) if SENT.exists() else set()
    papers = [p for p in data.get("pubs", []) if p.get("url") and p["url"] not in sent]
    if not papers:
        print("No new papers since the last digest; nothing to send.")
        return 0
    subject, body = build(data, papers)

    if args.dry_run:
        print(f"Subject: {subject}\n\n{body}")
        return 0
    key = os.environ.get("BUTTONDOWN_API_KEY")
    if not key:
        print("BUTTONDOWN_API_KEY is not set; skipping the email (data.json is still updated).")
        return 0

    r = requests.post(API, timeout=60,
                      headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
                      json={"subject": subject, "body": body, "status": "draft" if args.draft else "about_to_send"})
    if r.status_code >= 300:
        print(f"Buttondown rejected the email: HTTP {r.status_code}\n{r.text[:800]}", file=sys.stderr)
        return 1
    SENT.write_text(json.dumps(sorted(sent | {p["url"] for p in papers})[-SENT_CAP:], indent=0), encoding="utf-8")
    print(f"{'Drafted' if args.draft else 'Sent'} '{subject}' with {len(papers)} papers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
