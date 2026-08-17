#!/usr/bin/env python3
"""
Daily job finder.

Sources:
  1. LinkedIn guest job API (no auth) - broad sweep across keyword x location matrix
  2. Greenhouse board API - direct from target companies
  3. Ashby posting API - direct from target companies

Dedupes against data/seen.json so each daily report only shows genuinely new roles.
Writes a scored markdown report to reports/YYYY-MM-DD.md.

Usage:
    python3 find_jobs.py              # daily mode (last 24h from LinkedIn)
    python3 find_jobs.py --first-run  # wider window (14 days), seeds seen.json
    python3 find_jobs.py --no-salary  # skip salary enrichment (much faster)
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from bs4 import BeautifulSoup  # html.parser only - avoids an lxml build dependency

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "data")
REPORTS_DIR = os.path.join(ROOT, "reports")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------------------------------------------------------------- utilities

def log(msg):
    print(f"  {msg}", file=sys.stderr)


def get(url, timeout=25, retries=2):
    """GET a URL, returning decoded text or None. Never raises."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/json,application/xhtml+xml,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - a dead source must not kill the run
            if attempt == retries:
                log(f"fetch failed ({type(exc).__name__}) {url[:90]}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def post_json(url, body, timeout=25, retries=1):
    """POST a JSON body, returning decoded text or None. Never raises.

    Workday's job API is POST-only, so get() cannot reach it.
    """
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body.encode(), headers={
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - a dead source must not kill the run
            if attempt == retries:
                log(f"post failed ({type(exc).__name__}) {url[:90]}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def job_key(company, title, location=None):
    """Stable identity for a posting across sources (LinkedIn ids churn).

    Location is deliberately excluded: the same req is routinely posted once per
    eligible state/city, and we only want to show Dan the role once.
    """
    raw = f"{norm(company)}|{norm(title)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def has_word(needle, haystack):
    """Substring match that respects word boundaries.

    Without this, 'erp' matches 'Enterprise' and 'film' matches 'Thin Films' -
    both of which put junk at the top of the first report.
    """
    if not needle or not haystack:
        return False
    pat = r"(?<![a-z0-9])" + re.escape(needle.strip()) + r"(?![a-z0-9])"
    return re.search(pat, haystack, re.I) is not None


def env_int(name, fallback):
    """Env var wins over config.

    Lets the committed config stay free of personal salary figures: the real
    thresholds arrive as GitHub secrets at run time.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        return int(float(raw.replace(",", "").replace("$", "")))
    except ValueError:
        log(f"{name}={raw!r} is not a number - using config value {fallback}")
        return fallback


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


# ---------------------------------------------------------------- sources

def fetch_linkedin(cfg, tpr_seconds):
    """Sweep the LinkedIn guest API across every keyword x location pair."""
    jobs = []
    li = cfg["linkedin"]
    pairs = []
    for path_id, path in cfg["paths"].items():
        for kw in path["keywords"]:
            for loc in li["locations"]:
                pairs.append((path_id, kw, loc))

    log(f"LinkedIn: {len(pairs)} keyword x location queries")
    for i, (path_id, kw, loc) in enumerate(pairs, 1):
        for page in range(li["max_pages_per_query"]):
            params = {
                "keywords": kw,
                "location": loc["name"],
                "f_TPR": f"r{tpr_seconds}",
                "start": page * 25,
            }
            if loc.get("remote_filter"):
                params["f_WT"] = loc["remote_filter"]
            url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
                   + urllib.parse.urlencode(params))
            html = get(url)
            time.sleep(li["request_delay_seconds"])
            if not html or "base-card" not in html:
                break

            soup = BeautifulSoup(html, "html.parser")
            cards = soup.find_all("div", class_=re.compile("base-card"))
            if not cards:
                break
            for card in cards:
                t = card.find(["h3"], class_=re.compile("base-search-card__title"))
                c = card.find(["h4"], class_=re.compile("base-search-card__subtitle"))
                l = card.find("span", class_=re.compile("job-search-card__location"))
                a = card.find("a", class_=re.compile("base-card__full-link"))
                d = card.find("time")
                if not (t and c):
                    continue
                link = a["href"].split("?")[0] if a and a.has_attr("href") else ""
                jobs.append({
                    "title": clean(t.get_text()),
                    "company": clean(c.get_text()),
                    "location": clean(l.get_text()) if l else "",
                    "url": link,
                    "posted": (d.get("datetime") if d else "") or "",
                    "source": "LinkedIn",
                    "matched_path": path_id,
                    "matched_kw": kw,
                })
            if len(cards) < 10:
                break
        if i % 10 == 0:
            log(f"  ...{i}/{len(pairs)} queries, {len(jobs)} raw hits")
    return jobs


def fetch_greenhouse(boards):
    jobs = []
    for board in boards:
        raw = get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for j in payload.get("jobs", []):
            jobs.append({
                "title": clean(j.get("title")),
                "company": board,
                "location": clean((j.get("location") or {}).get("name", "")),
                "url": j.get("absolute_url", ""),
                "posted": (j.get("updated_at") or "")[:10],
                "source": "Greenhouse",
                "matched_path": None,
                "matched_kw": None,
            })
        time.sleep(0.3)
    return jobs


def fetch_ashby(boards):
    jobs = []
    for board in boards:
        raw = get(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for j in payload.get("jobs", []):
            jobs.append({
                "title": clean(j.get("title")),
                "company": board,
                "location": clean(j.get("location", "")),
                "url": j.get("jobUrl", ""),
                "posted": (j.get("publishedAt") or "")[:10],
                "source": "Ashby",
                "matched_path": None,
                "matched_kw": None,
            })
        time.sleep(0.3)
    return jobs


def fetch_lever(boards):
    jobs = []
    for board in boards:
        raw = get(f"https://api.lever.co/v0/postings/{board}?mode=json")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for j in payload:
            cats = j.get("categories") or {}
            jobs.append({
                "title": clean(j.get("text")),
                "company": board,
                "location": clean(cats.get("location", "")),
                "url": j.get("hostedUrl", ""),
                # createdAt is epoch milliseconds.
                "posted": (datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc)
                           .strftime("%Y-%m-%d") if j.get("createdAt") else ""),
                "source": "Lever",
                "matched_path": None,
                "matched_kw": None,
            })
        time.sleep(0.3)
    return jobs


def fetch_workday(boards):
    """Workday's CxS endpoint. POST-only, paginated, 20 per page.

    Most of the orgs in FEED_UPDATE_SPEC §4 (Liberty Media, the F1 teams, Blue
    Yonder, Körber) are NOT here - they were probed against Greenhouse, Lever,
    Ashby and Workday on 2026-08-17 and 404'd on all four. See
    config.direct_career_sites; those stay a manual check by the skill.
    """
    jobs = []
    for board in boards:
        tenant, site = board["tenant"], board["site"]
        host = board.get("host", "wd1")
        company = board.get("company", tenant)
        url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        offset = 0
        while offset < 200:  # a board this size never legitimately exceeds this
            body = json.dumps({"limit": 20, "offset": offset, "searchText": ""})
            raw = post_json(url, body)
            if not raw:
                break
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                break
            postings = payload.get("jobPostings") or []
            if not postings:
                break
            for j in postings:
                path = j.get("externalPath") or ""
                jobs.append({
                    "title": clean(j.get("title")),
                    "company": company,
                    "location": clean(j.get("locationsText", "")),
                    "url": (f"https://{tenant}.{host}.myworkdayjobs.com/en-US/{site}{path}"
                            if path else ""),
                    # postedOn is relative prose ("Posted 3 Days Ago"), not a date.
                    # Leave it blank rather than invent a timestamp.
                    "posted": "",
                    "source": "Workday",
                    "matched_path": None,
                    "matched_kw": None,
                })
            offset += 20
            if offset >= payload.get("total", 0):
                break
            time.sleep(0.4)
    return jobs


# ---------------------------------------------------------------- filtering

def location_verdict(job, cfg):
    """Classify a role's location into a geo tier. Returns (tier_name, flag).

    Returns (None, None) to exclude. Replaces the old boolean location_ok():
    "Denver or remote, no relocation" became a tiered model on 2026-08-17.

      core     - remote (US), Colorado, Denver metro. Hybrid/on-site included on
                 purpose; Dan wants an office.
      relocate - Atlanta, San Diego. Included but flagged, so the cost is visible.
      passion  - F1 cities and LA. Only worth a move for the right kind of role,
                 so each gate requires a matching path OR a matching employer.

    The employer half of a passion gate is not redundant. "Technical Program
    Manager, Studio Technology" classifies as product_program under longest-match,
    so gating LA on path alone would exclude exactly the roles that make LA worth
    considering at all.
    """
    lf = cfg["location_filters"]
    tiers = lf["geo_tiers"]
    loc = (job.get("location") or "").lower()
    title = (job.get("title") or "").lower()
    company = (job.get("company") or "").lower()
    blob = f"{loc} {title}"

    if any(m in blob for m in lf["reject_markers"]):
        return None, None
    # Non-US postings frequently say "Remote" too - drop them before that check.
    core = tiers["core"]

    def in_core_state():
        # Matches both the spelled-out name and the ", CO" postal form.
        for st in core.get("states", []):
            if has_word(st, loc):
                return True
            if len(st) == 2 and re.search(rf",\s*{re.escape(st.lower())}\b", loc):
                return True
        return False

    if any(has_word(c, loc) for c in lf["reject_countries"]):
        if not in_core_state():
            return None, None

    if any(m in loc for m in core["metros"]):
        return "core", core.get("flag")
    if in_core_state():
        return "core", core.get("flag")
    if core.get("allow_remote", True) and is_remote(loc, title, lf):
        return "core", core.get("flag")

    relo = tiers["relocate"]
    if any(m in loc for m in relo["metros"]):
        return "relocate", relo.get("flag")

    passion = tiers["passion"]
    for gate in passion["gates"]:
        if not any(m in loc for m in gate["metros"]):
            continue
        if job.get("matched_path") in gate["paths"]:
            return "passion", passion.get("flag")
        if any(has_word(c, company) for c in gate["companies"]):
            return "passion", passion.get("flag")
        # In one of these cities but not for a reason worth moving for.
        return None, None

    return None, None


def is_remote(loc, title, lf):
    """True only for genuinely location-flexible postings.

    Deliberately strict: "San Mateo, CA, United States" is an onsite role that
    merely names the country, and treating it as remote floods the report with
    Bay Area jobs Dan cannot take.
    """
    if any(has_word(m, f"{loc} {title}") for m in lf["remote_markers"]):
        return True
    bare = re.sub(r"[^a-z ]", " ", loc).strip()
    bare = re.sub(r"\s+", " ", bare)
    return bare in {"united states", "us", "usa", "united states of america", "nationwide"}


def seniority_ok(job, cfg):
    t = (job.get("title") or "").lower()
    return not any(has_word(r, t) for r in cfg["seniority"]["reject"])


def classify_path(job, cfg):
    """Assign a job to the path whose title signals it matches most specifically.

    Longest match wins, and that is load-bearing rather than incidental: it is what
    keeps "Warehouse Management System Manager" in warehouse_systems while
    "Warehouse Manager" stays in ops_leadership. Adding a short signal that
    overlaps a longer one in another path will silently re-route roles.
    """
    t = (job.get("title") or "").lower()
    best, best_len = None, 0
    for path_id, path in cfg["paths"].items():
        for sig in path["title_signals"]:
            if has_word(sig, t) and len(sig) > best_len:
                best, best_len = path_id, len(sig)
    if best:
        return best

    # Fall back to the employer. Liberty Media (F1's owner, HQ'd in Englewood CO)
    # posts corporate and commercial roles whose titles carry no motorsport signal
    # whatsoever - "Director, Commercial Operations" matches nothing. Without this
    # they get matched_path=None and are dropped before scoring, which would hide
    # the most reachable F1 opportunity Dan has, the one needing no relocation.
    # domain_bonus cannot cover this: it only applies after a path is assigned.
    company = (job.get("company") or "").lower()
    for path_id, companies in cfg.get("company_path_fallback", {}).items():
        if path_id.startswith("_") or path_id not in cfg["paths"]:
            continue
        if any(has_word(c, company) for c in companies):
            return path_id
    return None


# ---------------------------------------------------------------- scoring

def score(job, cfg):
    """Score a job against Dan's profile. Returns (score, reasons).

    The path is always re-derived from the title, never taken from the LinkedIn
    keyword that surfaced it - LinkedIn's matching is fuzzy enough that a search
    for "SAP Program Manager" returns security engineering roles.
    """
    pts, why = 0.0, []
    title = (job.get("title") or "").lower()
    company = (job.get("company") or "").lower()
    loc = (job.get("location") or "").lower()

    path_id = job.get("matched_path")
    if not path_id or path_id not in cfg["paths"]:
        return 0.0, ["no path signal in title"]

    path = cfg["paths"][path_id]
    hits = [s for s in path["title_signals"] if has_word(s, title)]
    if hits:
        pts += 4 * path["weight"]
        why.append(f"{path['label']} — “{hits[0].strip()}”")
    else:
        # No title signal, so the path came from the employer fallback in
        # classify_path(). Re-checking signals here would score every one of those
        # roles 0.0 and undo the fallback entirely - which is exactly what happened
        # to "Director, Commercial Operations" at Liberty Media in testing.
        fallback = cfg.get("company_path_fallback", {}).get(path_id, [])
        if not any(has_word(c, company) for c in fallback):
            return 0.0, ["path signal did not survive word-boundary check"]
        pts += 4 * path["weight"]
        why.append(f"{path['label']} — employer match, not title")

    # Seniority is tiered as of 2026-08-17: Dan is at the ceiling of the manager
    # band doing manager-of-managers work, so the feed aims a level up. Longest
    # match wins, which is what stops "Associate Manager" scoring as "manager".
    sen = cfg["seniority"]
    best_tier, best_len = None, 0
    for tier_name, tier in sen["tiers"].items():
        for term in tier["terms"]:
            if has_word(term, title) and len(term) > best_len:
                best_tier, best_len = tier_name, len(term)
    if best_tier:
        pts += sen["tiers"][best_tier]["points"]
        why.append(f"seniority: {best_tier.replace('_', ' ')}")

    # Stacks on top of the tier rather than replacing it, so an IC title lands
    # below min_score_to_report without ever being hard-rejected. Dan chose
    # down-weight over drop specifically to keep this reversible.
    ic = sen.get("ic_penalty")
    if ic:
        ic_hit = next((t for t in ic["terms"] if has_word(t, title)), None)
        if ic_hit:
            pts += ic["points"]
            why.append(f"individual-contributor signal: {ic_hit}")

    db = cfg["domain_bonus"]
    hit = next((c for c in db["companies"] if has_word(c, company)), None)
    if hit:
        pts += 3
        why.append(f"target company: {hit}")
    else:
        tw = next((w for w in db["title_words"] if has_word(w, title)), None)
        if tw:
            pts += 1.5
            why.append(f"domain of interest: {tw}")

    # Big-4 SAP practice roles. A penalty, not a drop - a legitimate non-consulting
    # role at one of these firms should still be visible, just not near the top.
    ep = cfg.get("employer_penalties")
    if ep:
        ep_hit = next((c for c in ep["companies"] if has_word(c, company)), None)
        if ep_hit:
            pts += ep["points"]
            why.append(f"consulting practice: {ep_hit}")

    tier = job.get("geo_tier")
    if tier:
        adj = cfg["location_filters"]["geo_tiers"][tier].get("score_adj", 0)
        if adj:
            pts += adj
        label = {"core": "core geography", "relocate": "would require relocating",
                 "passion": "passion-track city"}.get(tier, tier)
        why.append(label)

    # What Dan can prove on paper after the 2026-08-16 rebuild. Tableau and Power BI
    # came out with the analytics path; SQL stays, because it is a real skill and it
    # helps systems and TPM roles score. "transformation" came out too - it was
    # pulling the Big-4 functional roles the sap-erp reframe exists to avoid.
    proof = ["sap", "erp", "s/4hana", "s4hana", "deployment", "warehouse", "wms",
             "warehouse systems", "supply chain", "sql", "readiness", "cutover",
             "release", "product owner", "backlog", "uat", "governance",
             "logistics", "distribution"]
    proof_hits = [p for p in proof if has_word(p, title)]
    if proof_hits:
        pts += 1.5
        why.append(f"proven on résumé: {proof_hits[0]}")

    return round(pts, 1), why


# "a year" belongs here: LinkedIn renders Greenhouse/Lever ranges as
# "$156,000 - $196,000 a year", which matched none of the original cues. That
# silently read as "no compensation posted" - survivable when it only cost the
# role a score, disqualifying now that a missing range drops the role outright.
COMP_CUE = re.compile(
    r"(salary|compensation|pay range|base pay|pay band|hiring range|annual|"
    r"per year|a year|/yr|yearly|per annum|USD)",
    re.I)

# LinkedIn appends a "People also viewed" rail of other postings, each with its
# own salary. Those figures sit near no comp cue today, so they are ignored by
# luck rather than design - and one loosened cue away from being read as this
# job's pay. Cut the page at the rail instead of relying on that.
PAGE_TAIL = re.compile(
    r"(people also viewed|similar jobs|more jobs like this|recommended for you|"
    r"jobs you may be interested in|explore collaborative articles)", re.I)


def strip_page_chrome(text):
    """Drop everything after the job description - see PAGE_TAIL."""
    if not text:
        return text
    m = PAGE_TAIL.search(text)
    return text[:m.start()] if m else text

RANGE_RE = re.compile(
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s?(k\b)?\s*(?:-|–|—|to|and)\s*\$?\s?([\d][\d,]*(?:\.\d+)?)\s?(k\b)?",
    re.I)

SINGLE_RE = re.compile(
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s?(k\b)?", re.I)


def _amount(raw, k_flag):
    try:
        v = float(raw.replace(",", ""))
    except ValueError:
        return None
    if k_flag:
        v *= 1000
    # A bare "150" next to a $ in a comp sentence means $150k.
    if v < 1000:
        v *= 1000
    return int(v) if 40000 <= v <= 900000 else None


def extract_salary(text):
    """Pull an annual salary range out of a job page. Returns (lo, hi).

    Only looks at text near a compensation cue - job posts are full of unrelated
    dollar figures ("$200M in property", "$2.2MM savings") that otherwise poison
    the range.
    """
    if not text:
        return None, None
    text = re.sub(r"\s+", " ", text)

    windows = []
    for cue in COMP_CUE.finditer(text):
        windows.append(text[max(0, cue.start() - 200): cue.start() + 300])
    if not windows:
        return None, None

    vals = []
    for w in windows:
        for m in RANGE_RE.finditer(w):
            lo = _amount(m.group(1), m.group(2))
            hi = _amount(m.group(3), m.group(4))
            if lo and hi and lo <= hi:
                vals += [lo, hi]
    if not vals:  # fall back to single figures inside comp windows
        for w in windows:
            for m in SINGLE_RE.finditer(w):
                v = _amount(m.group(1), m.group(2))
                if v:
                    vals.append(v)
    if not vals:
        return None, None
    return min(vals), max(vals)


TRAVEL_RE = re.compile(r"(\d{1,3})\s?%\s*(?:of\s+the\s+time\s+)?travel|travel[^.]{0,40}?(\d{1,3})\s?%",
                       re.I)


def extract_travel(text):
    """Highest travel percentage mentioned, or None. Dan's ceiling is 50%."""
    if not text:
        return None
    pcts = []
    for m in TRAVEL_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= v <= 100:
            pcts.append(v)
    return max(pcts) if pcts else None


def enrich_details(jobs, cfg, limit):
    """Fetch detail pages for the top N jobs; attach salary range and travel load.

    Sets job["read"] only when the page actually came back and was parsed. Comp
    and travel are hard filters now, so "no salary found" and "never looked" have
    to stay distinguishable - the first is a reason to drop a role, the second is
    a reason to try again tomorrow.
    """
    done = 0
    for job in jobs:
        if done >= limit:
            break
        if not job.get("url"):
            continue
        html = get(job["url"], timeout=20, retries=1)
        time.sleep(0.8)
        done += 1
        if not html:
            continue
        text = strip_page_chrome(
            re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")))
        job["read"] = True
        lo, hi = extract_salary(text)
        if lo:
            job["salary_lo"], job["salary_hi"] = lo, hi
            job["meets_floor"] = hi is not None and hi >= cfg["candidate"]["comp_floor"]
        tv = extract_travel(text)
        if tv is not None:
            job["travel_pct"] = tv
    return jobs


def apply_hard_filters(jobs, cfg, stats):
    """Drop roles that fail Dan's non-negotiables. Returns the survivors.

    Every dropped job is tagged with j["dropped_by"] so main() can tell the two
    outcomes apart when it writes seen.json: a role judged and rejected is
    settled and should never surface again, but a role we could not read is
    unjudged and must stay eligible for the next run.
    """
    hf = cfg["candidate"].get("hard_filters", {})
    floor = cfg["candidate"]["comp_floor"]
    max_travel = cfg["candidate"]["max_travel_pct"]
    kept = []
    for j in jobs:
        if not j.get("read"):
            j["dropped_by"] = "unread"
            stats["dropped_unread"] += 1
            continue
        top = j.get("salary_hi") or j.get("salary_lo")
        if hf.get("require_posted_comp", True) and not top:
            j["dropped_by"] = "no_comp"
            stats["dropped_no_comp"] += 1
            continue
        if hf.get("require_high_end_above_floor", True) and top and top < floor:
            j["dropped_by"] = "below_floor"
            stats["dropped_below_floor"] += 1
            continue
        if (hf.get("enforce_travel_ceiling", True)
                and j.get("travel_pct") is not None
                and j["travel_pct"] > max_travel):
            j["dropped_by"] = "travel"
            stats["dropped_travel"] += 1
            continue
        kept.append(j)
    return kept


# ---------------------------------------------------------------- reporting

def money(v):
    return f"${v:,.0f}" if v else "—"


def location_cell(job):
    """Location with a 🧳 marker when taking the role would mean moving.

    Rendered inline rather than as its own column: notify_email.py passes the
    markdown table through as-is, so a column change risks the email layout.
    """
    loc = job.get("location") or "not stated"
    return f"{loc} 🧳" if job.get("geo_flag") == "relocation" else loc


def salary_cell(job, floor, target):
    lo, hi = job.get("salary_lo"), job.get("salary_hi")
    if not lo:
        return "not posted"
    rng = money(lo) if lo == hi else f"{money(lo)}–{money(hi)}"
    top = hi or lo
    if top >= target:
        return f"{rng} ✅"
    if top >= floor:
        return f"{rng} ⚠️"
    return f"{rng} ❌"


def write_report(jobs, cfg, first_run, stats):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(REPORTS_DIR, f"{today}.md")
    # Never clobber an earlier report from the same day - a second run only ever
    # contains the delta, and silently replacing the morning's list loses it.
    n = 2
    while os.path.exists(path):
        path = os.path.join(REPORTS_DIR, f"{today}-{n}.md")
        n += 1
    floor = cfg["candidate"]["comp_floor"]
    target = cfg["candidate"]["comp_target"]
    top_n = cfg["report"]["top_n_full_detail"]

    lines = []
    lines.append(f"# Job feed — {datetime.now().strftime('%A, %B %-d, %Y')}")
    lines.append("")
    window = "last 14 days (first run)" if first_run else "last 24 hours"
    geo = "remote/Denver core · Atlanta & San Diego flagged 🧳"
    bar = (f"comp posted and topping out at {money(floor)}+ · "
           f"{geo} · travel ≤{cfg['candidate']['max_travel_pct']}%")
    if stats.get("filters_skipped"):
        bar = (f"{geo} · **comp and travel filters skipped** "
               f"(run without detail fetches — figures below are unverified)")
    lines.append(f"*{len(jobs)} new roles matched* · window: {window} · {bar}")
    lines.append("")
    lines.append(f"<sub>Scanned {stats['raw']} raw postings from LinkedIn + "
                 f"{stats['boards']} company boards. "
                 f"{stats['dropped_loc']} dropped on location, "
                 f"{stats['dropped_sen']} on seniority, "
                 f"{stats['dropped_seen']} already seen.</sub>")
    lines.append("")

    # Every one of these was a role that scored well enough to report and was then
    # thrown out on comp or travel. Showing the count is the only way to tell an
    # honestly quiet day from filters that are set too tight.
    cut = (stats.get("dropped_no_comp", 0) + stats.get("dropped_below_floor", 0)
           + stats.get("dropped_travel", 0))
    if cut:
        lines.append(f"<sub>**{cut} scoring roles were filtered out:** "
                     f"{stats['dropped_no_comp']} posted no compensation, "
                     f"{stats['dropped_below_floor']} topped out below your "
                     f"{money(floor)} floor, "
                     f"{stats['dropped_travel']} exceeded your "
                     f"{cfg['candidate']['max_travel_pct']}% travel ceiling. "
                     f"Loosen these in `config.candidate.hard_filters`.</sub>")
        lines.append("")
    if stats.get("dropped_unread"):
        lines.append(f"<sub>{stats['dropped_unread']} roles could not be opened to "
                     f"check comp or travel, so they were held back rather than "
                     f"shown unverified. They stay eligible and will be retried on "
                     f"the next run.</sub>")
        lines.append("")

    # A blocked source and a genuinely quiet day produce the same empty report.
    # Say which one this is, loudly, or a silent outage reads as good news.
    if stats.get("degraded"):
        lines.append("> **⚠️ LinkedIn returned nothing this run.** Every other source "
                     "worked, so this is almost certainly LinkedIn blocking the "
                     "runner's IP rather than an empty market. Roughly two-thirds of "
                     "normal coverage is missing from this report — treat it as "
                     "partial, not as a quiet day.")
        lines.append("")

    if not jobs:
        if stats.get("degraded"):
            lines.append("No new roles — but see the warning above. This report is "
                         "not trustworthy as a signal that there was nothing to find.")
        elif cut:
            lines.append(f"Nothing cleared the bar today — but {cut} roles scored well "
                         f"and were cut on comp or travel alone, as broken out above. "
                         f"If that keeps happening, the filters are doing more work "
                         f"than the market is.")
        else:
            lines.append("Nothing new cleared the bar today. That's normal on a Monday "
                         "or a holiday week — the sweep still ran and all sources "
                         "responded.")
        with open(path, "w") as f:
            f.write("\n".join(lines))
        return path, 0

    by_path = {}
    for j in jobs:
        by_path.setdefault(j.get("matched_path") or "other", []).append(j)

    lines.append("## Top matches")
    lines.append("")
    for j in jobs[:top_n]:
        label = cfg["paths"].get(j.get("matched_path"), {}).get("label", "Other")
        lines.append(f"### {j['title']} — {j['company'].title()}")
        lines.append("")
        lines.append(f"- **Path:** {label}")
        lines.append(f"- **Location:** {location_cell(j)}")
        lines.append(f"- **Comp:** {salary_cell(j, floor, target)}")
        if j.get("travel_pct") is not None:
            ceiling = cfg["candidate"]["max_travel_pct"]
            over = f" ⚠️ over your {ceiling}% ceiling" if j["travel_pct"] > ceiling else ""
            lines.append(f"- **Travel:** up to {j['travel_pct']}%{over}")
        lines.append(f"- **Score:** {j['score']} — {', '.join(j['why'][:4])}")
        lines.append(f"- **Source:** {j['source']}"
                     + (f" · posted {j['posted'][:10]}" if j.get("posted") else ""))
        lines.append(f"- **Link:** {j['url']}")
        lines.append("")

    rest = jobs[top_n:]
    if rest:
        lines.append("## Also matched")
        lines.append("")
        lines.append("| Score | Role | Company | Location | Comp | Path |")
        lines.append("|---|---|---|---|---|---|")
        for j in rest:
            label = cfg["paths"].get(j.get("matched_path"), {}).get("label", "Other")
            short = label.split("(")[0].split("/")[0].strip()
            title = f"[{j['title']}]({j['url']})" if j["url"] else j["title"]
            lines.append(f"| {j['score']} | {title} | {j['company'].title()} | "
                         f"{location_cell(j)} | {salary_cell(j, floor, target)} | {short} |")
        lines.append("")

    lines.append("## By path")
    lines.append("")
    for path_id, group in sorted(by_path.items(), key=lambda kv: -len(kv[1])):
        label = cfg["paths"].get(path_id, {}).get("label", "Unclassified")
        lines.append(f"- **{label}** — {len(group)} new")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("<sub>✅ clears target · ⚠️ between floor and target · 🧳 would mean "
                 "relocating · every role here posts a range that tops out at or above "
                 "your floor and sits inside your travel ceiling.</sub>")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path, len(jobs)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first-run", action="store_true",
                    help="use a 14-day window and seed seen.json")
    ap.add_argument("--no-salary", action="store_true",
                    help="skip salary enrichment fetches")
    args = ap.parse_args()

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    # Personal salary thresholds live in GitHub secrets, not in the public config.
    cand = cfg["candidate"]
    cand["comp_floor"] = env_int("COMP_FLOOR", cand["comp_floor"])
    cand["comp_target"] = env_int("COMP_TARGET", cand["comp_target"])
    os.makedirs(DATA_DIR, exist_ok=True)

    seen = {}
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH) as f:
            raw_seen = json.load(f)
        # Accepts both the legacy {hash: {title, company, ...}} shape and the
        # current {hash: "YYYY-MM-DD"}. Only membership is ever tested, so the
        # legacy payload is dropped on the next write rather than migrated.
        seen = {k: (v if isinstance(v, str) else
                    (v.get("first_seen", "")[:10] if isinstance(v, dict) else ""))
                for k, v in raw_seen.items()}

    tpr = (cfg["linkedin"]["posted_within_seconds_firstrun"] if args.first_run
           else cfg["linkedin"]["posted_within_seconds_daily"])

    raw = fetch_linkedin(cfg, tpr)
    log(f"LinkedIn: {len(raw)} raw")

    tb = cfg["target_boards"]
    board_jobs = []
    board_jobs += fetch_greenhouse(tb["greenhouse"])
    board_jobs += fetch_ashby(tb["ashby"])
    board_jobs += fetch_lever(tb.get("lever", []))
    board_jobs += fetch_workday(tb.get("workday", []))
    log(f"Company boards: {len(board_jobs)} raw")

    raw_all = raw + board_jobs
    # Re-derive the path from the title for every job regardless of source. The
    # LinkedIn keyword that surfaced a posting is not evidence of what it is.
    for j in raw_all:
        j["matched_path"] = classify_path(j, cfg)
    raw_all = [j for j in raw_all if j["matched_path"]]

    stats = {"raw": len(raw) + len(board_jobs),
             "boards": (len(tb["greenhouse"]) + len(tb["ashby"])
                        + len(tb.get("lever", [])) + len(tb.get("workday", []))),
             "linkedin_raw": len(raw), "board_raw": len(board_jobs),
             # LinkedIn silently returning zero while the boards work means the IP
             # is blocked, not that the market is quiet. Runs on cloud IPs are the
             # likely case; the two states must never look alike downstream.
             "degraded": len(raw) == 0 and len(board_jobs) > 0,
             "dropped_loc": 0, "dropped_sen": 0, "dropped_seen": 0,
             "dropped_no_comp": 0, "dropped_below_floor": 0, "dropped_travel": 0,
             "dropped_unread": 0, "filters_skipped": False}

    kept, keys = [], set()
    for j in raw_all:
        tier, flag = location_verdict(j, cfg)
        if not tier:
            stats["dropped_loc"] += 1
            continue
        j["geo_tier"], j["geo_flag"] = tier, flag
        if not seniority_ok(j, cfg):
            stats["dropped_sen"] += 1
            continue
        k = job_key(j["company"], j["title"])
        if k in keys:
            continue
        keys.add(k)
        if k in seen:
            stats["dropped_seen"] += 1
            continue
        j["key"] = k
        s, why = score(j, cfg)
        if s < cfg["report"]["min_score_to_report"]:
            continue
        j["score"], j["why"] = s, why
        kept.append(j)

    kept.sort(key=lambda x: -x["score"])
    log(f"Kept {len(kept)} new scored roles")

    judged = kept
    if not args.no_salary and kept:
        log("Enriching salary + travel for top matches...")
        enrich_details(kept, cfg, cfg["report"]["enrich_salary_for_top_n"])
        kept = apply_hard_filters(kept, cfg, stats)
        kept.sort(key=lambda x: -x["score"])
        log(f"{len(kept)} cleared the hard filters "
            f"({stats['dropped_no_comp']} no comp posted, "
            f"{stats['dropped_below_floor']} under floor, "
            f"{stats['dropped_travel']} over travel ceiling, "
            f"{stats['dropped_unread']} unread - held for next run)")
    else:
        # --no-salary skips the detail fetches, so there is no evidence to filter
        # on. Report everything rather than dropping roles that were never judged.
        stats["filters_skipped"] = True

    path, n = write_report(kept, cfg, args.first_run, stats)

    now = datetime.now(timezone.utc).isoformat()
    today_str = now[:10]
    # Roles that were read and rejected are settled - record them so tomorrow's
    # run does not spend another detail fetch reaching the same verdict. Roles we
    # could not read are deliberately left out of seen.json so they come back.
    for j in judged:
        if j.get("dropped_by") in (None, "no_comp", "below_floor", "travel"):
            seen[j["key"]] = today_str
    with open(SEEN_PATH, "w") as f:
        json.dump(dict(sorted(seen.items())), f, indent=0)

    # Stable pointer to the newest report, for both the mailer and a human.
    # Created here rather than in daily_run.sh so it also exists in CI.
    try:
        latest = os.path.join(REPORTS_DIR, "LATEST.md")
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(os.path.basename(path), latest)
    except OSError as exc:
        log(f"could not update LATEST.md: {exc}")

    # Structured summary for notify_email.py. Parsing the markdown back out would
    # be brittle; this is the contract between the two scripts.
    top = kept[0] if kept else None
    with open(os.path.join(DATA_DIR, "last_run.json"), "w") as f:
        json.dump({
            "finished_utc": now,
            "report": os.path.relpath(path, ROOT),
            "new_count": n,
            "linkedin_raw": stats["linkedin_raw"],
            "board_raw": stats["board_raw"],
            "degraded": stats["degraded"],
            "top": ({"title": top["title"], "company": top["company"].title(),
                     "score": top["score"]} if top else None),
        }, f, indent=1)

    print(path)
    print(f"{n} new roles")


if __name__ == "__main__":
    main()
