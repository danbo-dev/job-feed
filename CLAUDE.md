# job-feed — the sweep engine

Public repo. Runs the daily job search in GitHub Actions and emails the report.
Career documents (résumés, positioning) live in the **private** `job-search` repo.

## Why public

Private repos consume Dan's 2,000 free Actions minutes/month. He exhausted them in
July 2026 and a private workflow was refused outright. Public repos are unlimited.
His public dashboards kept running while the quota was maxed — that is the tell.

Nothing personal is committed here. Verified by scan: no name, phone, email,
employer, or salary figure.

## Secrets, not config

| Secret | Falls back to |
|---|---|
| `EMAIL_TO`, `EMAIL_FROM` | `config.email.*` (empty here) |
| `EMAIL_PASSWORD` | none — **Dan must set this**; until then delivery is skipped |
| `COMP_FLOOR`, `COMP_TARGET` | `config.candidate.*` (0 placeholders here) |

Everything else — paths, keywords, boards, filters, weights — is in `config.json`
and safe to edit in the open.

## Privacy decisions worth keeping

- **`data/seen.json` stores hashes and dates only.** The code tests key membership
  and never reads values (`find_jobs.py`, `if k in seen`). Storing titles would
  make this repo a public daily log of what Dan is tracking, for zero benefit.
- **`reports/` is gitignored.** Email is the only copy.
- Committing the hashed `seen.json` daily also keeps the repo "active", which stops
  GitHub disabling the schedule after 60 days of inactivity.

## Verified 2026-07-31

- LinkedIn's guest API **works from GitHub runners** — 847 raw postings. This was
  the main unknown when moving to cloud.
- Dedupe survives across runs: `seen.json` went 46 → 48 → 49 over two back-to-back
  runs each scanning ~7,700 postings. Broken state would have re-reported all 48.
- `degraded` flag fires when LinkedIn returns 0 while boards return results, so a
  blocked source never reads as a quiet day.

## Gotchas

- Match on **word boundaries** — `erp` otherwise hits "Enterprise", `film` hits
  "Thin Films". Use `has_word()`.
- Never trust the LinkedIn keyword that surfaced a posting; re-derive the path from
  the title via `classify_path()`.
- "San Mateo, CA, United States" is **not** remote. See `is_remote()`.
- `extract_salary()` only reads figures near a compensation cue.
- Greenhouse board tokens either 200 or 404 — test before adding.
- `html.parser`, not `lxml`.
