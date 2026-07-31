# job-feed

A daily job search that runs itself. Sweeps LinkedIn's public guest API and ~31
company ATS boards, scores every posting against configurable career paths, filters
by location, salary, and travel load, then emails a report of **only what's new**.

Runs on GitHub Actions. No auth, no scraping logins, no paid APIs.

## What it does

1. **Sweeps** — 64 keyword × location queries against LinkedIn's guest endpoint,
   plus Greenhouse and Ashby board APIs for a list of target companies.
2. **Classifies** — assigns each posting to a career path from its title, using
   word-boundary matching (naive substring matching makes `erp` hit
   "Ent**erp**rise Security" and `film` hit "Thin **Film**s").
3. **Scores** — path fit, seniority, target company, location, and provable skills.
4. **Enriches** — fetches each match's detail page for the posted salary range and
   travel percentage, then demotes anything under the floor or over the travel
   ceiling.
5. **Dedupes** — against `data/seen.json`, keyed on company + title so one req
   posted to five cities is reported once and never repeats.
6. **Emails** — the report as inline-styled HTML.

## Configure

Everything tunable lives in `config.json`: career paths and their keywords, title
signals, target companies, location filters, seniority rules, scoring weights, and
report thresholds. No code changes needed to retarget the search.

Personal values are **not** in the config. They arrive as environment variables,
set as repository secrets:

| Secret | Purpose |
|---|---|
| `EMAIL_TO` | Where the report goes |
| `EMAIL_FROM` | Sending address |
| `EMAIL_PASSWORD` | Gmail **app password** — not an account password |
| `COMP_FLOOR` | Below this, a role is demoted |
| `COMP_TARGET` | At or above this, it's marked ✅ |

Each falls back to `config.json` when unset, so it also runs locally.

## Run it

```bash
pip install beautifulsoup4
python3 scripts/find_jobs.py              # last 24 hours
python3 scripts/find_jobs.py --first-run  # 14-day window, seeds the dedupe store
python3 scripts/find_jobs.py --no-salary  # skip detail fetches (fast, less useful)
python3 scripts/notify_email.py --dry-run # render the email to data/preview.html
```

The workflow runs daily at 13:15 UTC and can be triggered manually from the Actions
tab.

## Source health

LinkedIn's guest API needs no auth but rate-limits and may block datacenter IPs. If
it returns **zero** while the company boards return results, the run marks itself
`degraded`: the report gets a warning banner and the email subject changes to
`⚠️ LinkedIn blocked — partial results only`.

That distinction matters. A blocked source and a genuinely quiet day both produce an
empty report, and they mean opposite things.

## Privacy

`data/seen.json` stores **only opaque hashes and dates**. The code never reads
anything but key membership, so job titles, companies, and URLs are deliberately not
persisted — otherwise this repo would become a public, daily-updated log of every
role its owner is tracking. `reports/` is gitignored for the same reason; email is
the delivery channel.

## Notes

- Uses `html.parser`, not `lxml` — one fewer build dependency.
- Salary parsing only reads figures near a compensation cue. Job pages are full of
  unrelated dollar amounts, and "$200M in property" from a résumé bullet otherwise
  parses as a salary.
- Greenhouse board tokens are guesses that either 200 or 404. Before adding a
  company, check `https://boards-api.greenhouse.io/v1/boards/<token>/jobs`.
