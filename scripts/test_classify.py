#!/usr/bin/env python3
"""Classification, geography, and scoring cases. No network, runs in a second.

    python3 scripts/test_classify.py

Every case here failed at some point on 2026-08-17 or guards something that did.
The failures were all silent in production — a misrouted path or a geo exclusion
produces a plausible-looking report, just the wrong one. Run this after any edit to
config.json's paths, signals, weights, or geo tiers.

Adding a title_signal to one path can re-route titles in another, because
classify_path() resolves by LONGEST match. That is the single easiest way to break
this config, and the only cheap way to notice is to re-run these cases.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import find_jobs as fj  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# title, company, location, expected path, expected geo tier, should it report?
CASES = [
    # --- the lead path exists at all (it did not before 2026-08-17) ---
    ("Director, Warehouse Systems", "Kroger", "Denver, CO",
     "warehouse_systems", "core", True),
    ("Director, Distribution Technology", "Home Depot", "Atlanta, GA",
     "warehouse_systems", "relocate", True),
    ("Sr Manager, Supply Chain Systems", "Manhattan Associates", "Atlanta, GA",
     "warehouse_systems", "relocate", True),

    # --- the signal split: systems forms vs bare ops forms ---
    ("Warehouse Management System Manager", "Target", "Remote",
     "warehouse_systems", "core", True),
    ("Warehouse Operations Manager", "Amazon", "Aurora, CO",
     "ops_leadership", "core", True),
    ("Warehouse Manager", "Amazon", "Denver, CO",
     "ops_leadership", "core", True),

    # --- longest match nearly sent Dan's most common title to the wrong path ---
    ("SAP Program Manager", "Ball Corp", "Denver, CO", "sap_erp", "core", True),
    # ...and the Big-4 penalty must knock the same title below the threshold
    ("SAP Program Manager", "Deloitte", "Remote", "sap_erp", "core", False),

    # --- F1 without relocating: Liberty Media is in Englewood, CO ---
    # No motorsport signal in this title at all; the path comes from the employer,
    # and score() must honour that instead of re-checking title signals.
    ("Director, Commercial Operations", "Liberty Media", "Englewood, CO",
     "motorsport", "core", True),
    ("Race Operations Manager", "Las Vegas Grand Prix", "Las Vegas, NV",
     "ops_leadership", "passion", True),

    # --- passion cities stay shut for everything else ---
    ("Director of Operations", "Zappos", "Las Vegas, NV", "ops_leadership", None, False),
    ("Director of Operations", "Kaiser", "Los Angeles, CA", "ops_leadership", None, False),
    # LA opens on the EMPLOYER, not the path: this title is product_program.
    ("Technical Program Manager, Studio Technology", "Netflix", "Los Angeles, CA",
     "product_program", "passion", True),

    # --- the fallback path must stay visible, not silently score out ---
    ("Director of Operations", "Ball Corp", "Broomfield, CO",
     "ops_leadership", "core", True),

    # --- aim a level up: IC and first-line titles fall below the bar ---
    ("Supply Chain Analyst", "PepsiCo", "Denver, CO", "ops_leadership", "core", False),
    ("Associate Manager, Logistics", "PepsiCo", "Denver, CO",
     "ops_leadership", "core", False),

    # --- geography still excludes the rest of the country ---
    ("Senior Director, Supply Chain Systems", "Albertsons", "Boise, ID",
     "warehouse_systems", None, False),

    # --- plurals. has_word() is exact, so "Systems" does NOT match the signal
    # "warehouse management system" while it DOES match "warehouse management" -
    # the plural silently demoted this real posting to the fallback path. Both
    # forms are enumerated in config; do not "fix" this by loosening has_word(),
    # which would re-introduce "film" matching "Thin Films".
    ("Senior Director, Warehouse Management Systems & Labor Management",
     "Michaels", "Denver, CO", "warehouse_systems", "core", True),
    ("Distribution System Director", "Dot Foods", "Denver, CO",
     "warehouse_systems", "core", True),

    # --- real Denver-area titles that matched NO signal at all in the first
    # live sweep, so they were dropped before scoring ---
    ("Warehouse Director - Coors Field", "Aramark", "Denver, CO",
     "ops_leadership", "core", True),
    ("Director of Distribution and Warehousing", "Ball Corp", "Denver, CO",
     "ops_leadership", "core", True),
]


def main():
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    # Mirrors the GitHub secrets; the placeholders in config.json are zeros.
    cfg["candidate"]["comp_floor"] = 150000
    cfg["candidate"]["comp_target"] = 180000
    threshold = cfg["report"]["min_score_to_report"]

    failures = []
    print(f"{'title':<45} {'path':<19} {'tier':<9} {'score':>5} {'rpt':>4}")
    print("-" * 88)
    for title, company, location, want_path, want_tier, want_report in CASES:
        job = {"title": title, "company": company, "location": location}
        job["matched_path"] = fj.classify_path(job, cfg)
        tier, flag = fj.location_verdict(job, cfg)
        job["geo_tier"], job["geo_flag"] = tier, flag
        pts, _why = fj.score(job, cfg) if job["matched_path"] else (0.0, [])
        reports = bool(tier) and pts >= threshold

        problems = []
        if job["matched_path"] != want_path:
            problems.append(f"path={job['matched_path']} want={want_path}")
        if tier != want_tier:
            problems.append(f"tier={tier} want={want_tier}")
        if reports != want_report:
            problems.append(f"reports={reports} want={want_report}")
        if problems:
            failures.append((title, company, problems))

        mark = "" if not problems else "  <-- " + "; ".join(problems)
        print(f"{title[:44]:<45} {str(job['matched_path'])[:18]:<19} "
              f"{str(tier):<9} {pts:>5.1f} {'yes' if reports else '—':>4}{mark}")

    print("-" * 88)
    if failures:
        print(f"FAILED: {len(failures)} of {len(CASES)}")
        for title, company, problems in failures:
            print(f"  {title} @ {company}: {'; '.join(problems)}")
        return 1
    print(f"PASSED: all {len(CASES)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
