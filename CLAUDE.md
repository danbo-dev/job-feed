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
| `EMAIL_PASSWORD` | none. Must be a Gmail **app password** — the account password is rejected with `534 5.7.9 Application-specific password required` |
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

## Verified end-to-end 2026-07-31

Full pipeline confirmed working: **12 new roles swept, scored, emailed, received.**

- LinkedIn's guest API **works from GitHub runners** — 945 raw postings. This was
  the main unknown when moving to cloud.
- Email delivers and renders — checked in the actual Gmail inbox, not just the
  send log.
- Comp thresholds apply correctly: ⚠️ at $150–175K, ✅ at $142.6–297.2K,
  ❌ at $85.6–115.6K.
- Travel ceiling fires: three EY roles flagged "up to 60% ⚠️ over your 50%
  ceiling" and demoted to 6.0/5.2.
- Dedupe survives across runs (`5 already seen` on this run).
- `degraded` flag fires when LinkedIn returns 0 while boards return results, so a
  blocked source never reads as a quiet day.

### Two bugs found by testing, worth not repeating

1. **GitHub secrets are not ambient.** Only secrets listed in a step's `env:`
   block reach the process. `EMAIL_TO` and `COMP_FLOOR`/`COMP_TARGET` were set but
   unmapped, so the mailer aborted with "no recipient" and the sweep scored against
   placeholder `0` thresholds — marking every salary as clearing target.
2. **State must not advance on failed delivery.** Runs committed `seen.json` even
   when the email failed, marking roles seen that Dan never received; four were
   lost that way. `notify_email.py` now writes `data/delivered.flag` on success
   only, and the commit step skips `seen.json` without it.

## Gotchas

- Match on **word boundaries** — `erp` otherwise hits "Enterprise", `film` hits
  "Thin Films". Use `has_word()`.
- Never trust the LinkedIn keyword that surfaced a posting; re-derive the path from
  the title via `classify_path()`.
- "San Mateo, CA, United States" is **not** remote. See `is_remote()`.
- `extract_salary()` only reads figures near a compensation cue.
- Greenhouse board tokens either 200 or 404 — test before adding.
- `html.parser`, not `lxml`.
