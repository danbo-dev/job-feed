# job-feed — the sweep engine

Public repo. Runs the daily job search in GitHub Actions and emails the report.
Career documents (résumés, positioning) live in the **local-only** `job-search` repo
(it has no GitHub remote — see that repo's `CLAUDE.md`).

## Current state (2026-08-17) — retuned to the résumé rebuild

The 2026-08-16 résumé rebuild in `job-search` changed the strategy, and the feed was
still sourcing for the old one. Retuned per `job-search/FEED_UPDATE_SPEC.md`.

**Paths: analytics dropped, warehouse_systems added as the lead.**

| Path | wt | Notes |
|---|---|---|
| `warehouse_systems` | 1.2 | **New.** The lead résumé variant |
| `product_program` | 1.1 | Raised from 0.9 |
| `sap_erp` | 1.0 | Narrowed — business-side only |
| `ops_leadership` | 0.8 | Fallback |
| `motorsport` | 1.2 | Geo-gated. Equal to the lead path **on purpose** |
| `media_entertainment` | 1.0 | **New.** Geo-gated; exists so LA has something to gate on |
| ~~`data_analytics`~~ | — | **Deleted.** Undersold an L11 manager-of-managers |

### The bug this fixed

`warehouse-systems` is the *lead* résumé variant and had **no path at all**. A title
like "Director, Warehouse Systems" matched no `title_signal` anywhere — `sap_erp`
owned `warehouse management`, `ops_leadership` owned `warehouse operations`, and
neither matches "warehouse systems". `main()` drops every job with
`matched_path=None` before scoring, so the highest-conversion roles were being
**discarded silently**. That is the likely reason the feed skewed SAP and ops.

### Four things testing caught that review didn't

All four were found by the case table in the verification section below. Re-run it
after any config change; each of these fails silently in production.

1. **`SAP Program Manager` classified as `product_program`.** Longest match: bare
   `sap` is 3 chars, `program manager` is 15. Dan's single most common target title
   went to the wrong path. Fixed by adding multi-word signals (`sap program manager`,
   `erp program manager`, …) long enough to win.
2. **The Liberty Media fallback scored 0.0.** `classify_path()` falls back to the
   employer when no title signal matches, but `score()` then re-checked title signals
   and returned `0.0, ["path signal did not survive word-boundary check"]` — undoing
   the fallback completely. `score()` now recognises employer-assigned paths.
3. **`ops_leadership` at weight 0.7 went silent.** A Denver "Director of Operations"
   scored 6.8 against a 7.0 report threshold. That doesn't rank the fallback last, it
   deletes it. Restored to 0.8.
4. **Motorsport event orgs didn't match.** "Las Vegas Grand Prix" hit none of the
   gate's companies, so a race-ops role in a host city was excluded on geography.
   Added `grand prix` / `speedway` / `raceway`.

### Two more caught by the live sanity run

5. **Plurals silently misroute titles.** `has_word()` is exact on word boundaries, so
   "Warehouse Management **Systems**" does *not* match the signal `warehouse management
   system` — but it *does* match ops_leadership's shorter `warehouse management`. A real
   posting ("Senior Director, Warehouse Management Systems & Labor Management") was
   demoted to the fallback path by nothing but a trailing "s". Both forms are now
   enumerated. **Do not "fix" this by making `has_word()` plural-tolerant** — that
   re-introduces the documented `film` → "Thin **Film**s" bug.
6. **Real Denver titles matched no signal at all** and were dropped before scoring:
   "Warehouse Director – Coors Field", "Director of Distribution and Warehousing",
   "Distribution System Director". Added `warehouse director`, `warehousing`, and the
   singular `distribution system` / `supply chain system` / `fulfillment system`.

### What the first live run actually showed

`--first-run`, 14-day window: 8,839 raw → 66 scored → **37 reported**. Zero analytics
roles, confirming the path removal. But **zero warehouse_systems roles too**, which
looked like the retune had failed. It hadn't — the path classified 27 of 123 postings
in a targeted replay. All three lead-path roles in core geography were killed by the
**comp filters**, not by classification:

| Role | Score | Outcome |
|---|---|---|
| PwC — Warehouse Automation Sr. Manager | 6.8 | Big-4 penalty pushed it under the 7.0 bar — working as intended |
| EchoStar — Lead Product Owner, SC Technology | 8.8 | $96–137K, under the floor |
| RF-SMART — Senior Product Director, NetSuite WMS | **10.3** | **no posted comp** |

**`require_posted_comp` is now the binding constraint on the lead path.** 16 of 65
scored roles were dropped for no posted range this run. The third row is the cost made
concrete: the single best-scoring lead-path role was invisible purely because the
employer published no salary. That is Dan's toggle to loosen
(`config.candidate.hard_filters`), not one to change unilaterally — but if the lead
path keeps coming back empty, this is the reason, not the tuning.

A useful follow-up if it recurs: break the "scoring roles were filtered out" line down
**by path**, so a lead-path role lost to a missing salary is visible rather than
folded into an aggregate count.

### Geography — three tiers

`location_ok()` became `location_verdict() -> (tier, flag)`:

| Tier | Score | Where |
|---|---|---|
| `core` | +1.5 | Remote (US), Colorado, Denver metro — **hybrid/on-site included**, Dan wants an office |
| `relocate` | −0.5 🧳 | Atlanta (best core-career option), San Diego |
| `passion` | 0.0 🧳 | Austin/Miami/Vegas → motorsport; LA → media |

Two non-obvious properties, both load-bearing:

- **A passion gate matches on path OR employer.** Path alone is not enough: "Technical
  Program Manager, Studio Technology" classifies as `product_program`, so a path-only
  LA gate would exclude exactly the roles that justify LA.
- **Liberty Media is in Englewood, CO — it clears `core`, not `passion`.** F1 without
  relocating is the whole point, and `core` accepts any path. But Liberty's corporate
  titles ("Director, Commercial Operations") carry no motorsport signal, which is why
  `company_path_fallback` exists. `domain_bonus` can't do that job — it only applies
  *after* a path is assigned.

### Seniority — tiered, aimed a level up

Director +2.5 / Senior Manager +2.0 / Manager +1.0, resolved by longest match so
"Associate Manager" doesn't score as "manager". IC terms (analyst, specialist,
coordinator, associate manager, supervisor) carry **−2.5, stacked on top of** the
positive tier. Deliberately a penalty and not a `reject`: Dan chose down-weight over
drop so it stays reversible — a rejected role would be written to `seen.json` as
settled and never resurface.

### SAP exclusion is three-layered

Removing the two magnet keywords only stops *attracting* Big-4 functional roles; it
doesn't exclude them. So: magnet keywords removed, explicit `seniority.reject` terms
(`functional consultant`, `sap configuration`, `abap`, …), and `employer_penalties`
(−3) for the Big-4 firms — a penalty, not a drop, so a legitimate non-consulting role
at one of those firms stays visible. **The travel ceiling still does most of the work**
and is unchanged; those roles are the 40–80% travel ones.

### New sources

`fetch_lever()` and `fetch_workday()` added (Workday is POST-only — hence
`post_json()`). Verified live: **Manhattan Associates** (Workday, `manh`/`wd5`/`External`,
41 reqs) and **Spotify** (Lever). Everything else in `FEED_UPDATE_SPEC` §4 — Liberty
Media, the F1 teams, Blue Yonder, Körber, System Logistics, Netflix, Adobe, Unity —
**404'd on all four ATSs** and stays a manual check in `direct_career_sites`. That's a
recorded result; don't re-probe.

---

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
