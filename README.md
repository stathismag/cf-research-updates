# Corporate Finance Research Updates

Live site: https://cf-research-updates.netlify.app

Static site hosted on Netlify. A GitHub Action refreshes the paper listings on the 1st and 15th of each month;
conferences and jobs come from a Google Sheet fed by two Google Forms. Every commit (including the bot's)
triggers a Netlify redeploy.

## Files

```
index.html                   the site (edit only the SHEETS and FORMS links at the top of the script)
data.json                    paper listings, written by the Action
netlify.toml                 stops Netlify caching data.json
scripts/fetch_papers.py      pulls new papers from Crossref, tags them, writes data.json
scripts/config.json          journals, themes, keyword rules
.github/workflows/update.yml schedule for the script
```

## One-time setup

1. Repo → Settings → Secrets and variables → Actions → New repository secret: name `CROSSREF_EMAIL`, value your
   email (Crossref asks for one on automated requests). It stays out of the public code. Set `repo_url` in
   `scripts/config.json`; `contact_email` there is only a fallback for local runs.
2. Upload all files to the repo, keeping the folder structure.
3. Repo → Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save.
4. Actions tab → **Update research listings** → **Run workflow**. In the log, the "Verify ISSNs" step prints the
   journal name Crossref returns for each ISSN; if any says NOT FOUND or shows the wrong journal, fix the ISSN in
   `config.json` and run again. After 1–3 minutes a commit "Update listings …" appears and `data.json` is filled.
5. Connect the repo to Netlify (Add new project → Import from GitHub). No build command; publish directory `.`.
6. Netlify → Project configuration → Forms → **Enable form detection**, then redeploy once. Subscriber emails
   appear under Forms → `subscribe` (free tier: 100 submissions/month; email notifications can be added there).
7. Set up the Google Sheet for conferences and jobs (see below) and paste the four links into `index.html`.

## Conferences and jobs (Google Sheet)

Two Google Forms write to one spreadsheet with tabs `Conferences` and `Jobs`. Each response tab has an
`Approved` checkbox column added by hand (column J for conferences, column H for jobs). Two further tabs,
`Conferences (public)` and `Jobs (public)`, contain only approved rows via

```
=IFERROR(FILTER(Conferences!B2:I, Conferences!J2:J=TRUE),"")
=IFERROR(FILTER(Jobs!B2:G, Jobs!H2:H=TRUE),"")
```

and are published to the web as CSV (File → Share → Publish to web → that tab → CSV). Those two CSV links go into
`SHEETS` in `index.html`; the two form links go into `FORMS`.

Form questions, in order, with exactly these titles:

- Conference form: Event name, Organiser, Location, Start date, End date, Submission deadline, Website, Theme
  (dropdown: General, Climate Uncertainty, Biodiversity & Nature, Cybersecurity, Crypto & Digital Assets,
  Political Geography, AI & Technology, Supply Chains & Trade, ESG & Sustainability)
- Job form: Institution, Position title, Field, Location, Application deadline, Link to posting

Keep "Collect email addresses" off in both forms, because it inserts a column and shifts the letters above.
If you later add a question to a form, re-check the column letters in the FILTER formulas.

Routine: tick `Approved` to publish a row. It appears on the site within a few minutes. Past events and expired
deadlines are hidden automatically, so old rows can stay in the sheet.

## Routine maintenance

- Themes, areas, journals, keywords: edit `scripts/config.json`; the next run picks it up.
- Test locally: `pip install requests`, then `python scripts/fetch_papers.py --check` and
  `python scripts/fetch_papers.py --days 30`, and open `index.html` through a local web server
  (e.g. `python -m http.server`), since browsers block `fetch()` on `file://` URLs.

## How the paper data is collected

- Journal articles: free Crossref API, by journal ISSN. The DOI registration date is used as the online-first date.
  Each paper is matched against the keyword dictionaries in `config.json` for themes, and assigned the classic area
  with the most keyword hits. Management Science is only included when a paper matches at least one keyword.
- SSRN: SSRN has no public API. The script uses the DOIs SSRN registers with Crossref (`SSRN Electronic Journal`) and
  keeps titles matching corporate-finance keywords. Abstracts are usually missing there, so this list is approximate.
- If GitHub shows a notice that the scheduled workflow was disabled for inactivity, re-enable it in the Actions tab.
