# job-feed

A daily job search that runs itself. Sweeps LinkedIn's public guest API and ~33
company ATS boards, scores every posting against configurable career paths, filters
by location, salary, and travel load, then emails a report of **only what's new**.

Runs on GitHub Actions. No auth, no scraping logins, no paid APIs.

## What it does

1. **Sweeps** — 94 keyword × location queries against LinkedIn's guest endpoint,
   plus Greenhouse, Ashby, Lever, and Workday board APIs for a list of target
   companies.
2. **Classifies** — assigns each posting to a career path from its title, using
   word-boundary matching (naive substring matching makes `erp` hit
   "Ent**erp**rise Security" and `film` hit "Thin **Film**s"). Ties resolve to the
   **longest** matching signal, which is what keeps "Warehouse Management System
   Manager" and "Warehouse Manager" in different paths.
3. **Scores** — path fit, tiered seniority, target company, geography tier, and
   provable skills.
4. **Enriches** — fetches each match's detail page for the posted salary range and
   travel percentage, then drops anything under the floor or over the travel
   ceiling.
5. **Dedupes** — against `data/seen.json`, keyed on company + title so one req
   posted to five cities is reported once and never repeats.
6. **Emails** — the report as inline-styled HTML.

## Career paths (retuned 2026-08-17)

| Path | Weight | Role |
|---|---|---|
| `warehouse_systems` | 1.2 | **Lead.** Warehouse / supply-chain systems leadership |
| `product_program` | 1.1 | Co-lead. Technical program & product management |
| `sap_erp` | 1.0 | Business-side ERP program & deployment |
| `ops_leadership` | 0.8 | Fallback. Operations / supply chain |
| `motorsport` | 1.2 | Passion. Geography-gated |
| `media_entertainment` | 1.0 | Passion. Geography-gated, business/tech side only |

`data_analytics` was removed — see `CLAUDE.md`.

## Geography

Three tiers, replacing the old flat "Denver or remote" allow-list:

| Tier | Score | Where |
|---|---|---|
| `core` | +1.5 | Remote (US), Colorado, Denver metro — hybrid and on-site included |
| `relocate` | −0.5 | Atlanta, San Diego — included but flagged 🧳 in the report |
| `passion` | 0.0 | Austin/Miami/Las Vegas (motorsport only), LA (media only) — flagged 🧳 |

Anything else is excluded. A passion city unlocks on a **matching path *or* a matching
employer**; the employer half is not redundant, because a title like "Technical Program
Manager, Studio Technology" classifies as `product_program` under longest-match and a
path-only gate would exclude it.

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
| `COMP_FLOOR` | A role whose range tops out below this is dropped, not demoted |
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
- Workday is **POST-only** (`post_json()`, not `get()`), paginated 20 at a time, at
  `https://<tenant>.<host>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`.
- Several target employers have **no usable API** and are deliberately not in
  `target_boards` — they were probed against Greenhouse, Lever, Ashby, and Workday on
  2026-08-17 and 404'd on all four: Liberty Media, Formula 1, Haas, McLaren, Andretti,
  NASCAR, IndyCar, Blue Yonder, Körber, System Logistics, Netflix, Adobe, Unity. They
  live in `config.direct_career_sites` as a manual check. **That's a recorded result,
  not an unknown — don't re-probe those four.**
