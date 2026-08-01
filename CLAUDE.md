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

## Comp and travel are hard filters (2026-07-31)

After the first live run, Dan asked for three exclusions rather than score
penalties: **no posted comp, high end below the floor, and travel over 50% all
drop the role from the report.** Toggles live in
`config.candidate.hard_filters`; on a 14-day test sweep they cut 35 scored roles
to 16 (7 no comp, 6 under floor, 6 over ceiling).

The rule that keeps this safe: **a role is only dropped on evidence.**
`enrich_details()` sets `job["read"]` only when the detail page actually came
back, and `apply_hard_filters()` holds anything unread for the next run instead
of discarding it unjudged. `seen.json` records roles that were read and
rejected — a settled verdict shouldn't cost another fetch tomorrow — but never
records unread ones, so a fetch failure is a delay, not a deletion.

Because of that, `enrich_salary_for_top_n` is now a coverage floor, not a
nicety: anything past the cap is held back rather than reported unchecked. It's
at 120 against a `max_total_report` of 45.

`--no-salary` skips the filters entirely (no fetches means no evidence) and
says so in the report header.

### The bug this nearly buried

LinkedIn renders many ranges as `$156,000 - $196,000 a year`, and **"a year"
was not in `COMP_CUE`** — so those posts extracted as "no comp posted." Under
the old scoring that cost a role a few points. Under a hard filter it deletes
the role and marks it seen forever. One role in the test sweep was recovered by
adding the cue ($156–196K, clears the floor). Verified the other six no-comp
drops that run were genuinely blank.

Tightening cues raised a second risk, so the page is now cut at LinkedIn's
"People also viewed" rail before parsing — those neighbouring postings carry
their own salaries and were being ignored only because no comp cue happened to
sit near them.

Travel extraction was audited the same way and needed no change: every match on
the dropped roles was a real requirement ("Travel Requirements: Up to 60%",
"travel is estimated at 40-60%"). Big-4 SAP practice roles are what this filter
mostly removes, which matches the known constraint.

## Gotchas

- Match on **word boundaries** — `erp` otherwise hits "Enterprise", `film` hits
  "Thin Films". Use `has_word()`.
- Never trust the LinkedIn keyword that surfaced a posting; re-derive the path from
  the title via `classify_path()`.
- "San Mateo, CA, United States" is **not** remote. See `is_remote()`.
- `extract_salary()` only reads figures near a compensation cue — and comp is now
  a hard filter, so **a missing cue silently deletes roles**. Test any cue change
  against real pages before shipping it.
- The email escapes raw HTML except `<sub>`. Use markdown backticks for code
  spans; a literal `<code>` tag arrives as `&lt;code&gt;`.
- Greenhouse board tokens either 200 or 404 — test before adding.
- `html.parser`, not `lxml`.
