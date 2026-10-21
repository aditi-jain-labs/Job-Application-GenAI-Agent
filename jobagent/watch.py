"""Watch list monitor (run.py --watch): big companies in watch_companies.yaml, no referral.

Same rules as the referral monitor. It NEVER applies. For companies with a reader (jobagent/bigfour.py: PwC UK,
Deloitte UK, KPMG UK) it lists every open UK job, filters titles first (exclude_title_words, target titles),
reads the full listing, applies the skip rules (permanent only, minimum salary, sponsorship, DV clearance,
UK-nationality-only vetting), scores on the rules, sends 55+ to Claude, caps for missing essentials and
line management, and keeps jobs at match_threshold or above. Senior Manager, Associate Director and Director
grades are kept and marked "stretch". Each match gets a tailored Word CV and a row in
output/watch_jobs_available.csv (newest first), and a Mac notification says how many are new.
Companies without a reader are listed as "set up a job alert". Jobs looked at are remembered in
cache/watch_seen.json and not looked at again.
"""
import csv
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

import yaml

from . import bigfour, claude_score, llm, progress, review, rules, scoring, tailor
from .settings import CACHE_DIR, ROOT, WATCH_PATH, llm_settings, run_dir, today

SEEN = CACHE_DIR / "watch_seen.json"
FIELDS = ["Date", "Posted", "Company", "Job title", "Grade", "Location", "Score", "Link", "CV", "Applied", "Note"]
OFF_TARGET = "title is not a target role"
NATIONALITY = re.compile(
    r"sole\s+(uk|british)\s+nationals?|(uk|british)\s+nationals?\s+only|only\s+(open|available)\s+to\s+(uk|british)\s+(citizens|nationals)|"
    r"must\s+(be|hold)\s+(a\s+)?(sole\s+)?(uk|british)\s+(citizen|national)|(uk|british)\s+(citizenship|nationality)\s+(is\s+)?(required|essential|only)|"
    r"dual\s+nationals?\s+(are\s+)?not\s+eligible", re.I)
# Final outcomes: the job is not looked at again. Anything else (blocked, page errors, stopped) is retried next run.
FINAL = ("skipped", "eligible")


def load() -> tuple[dict, list[dict]]:
    data = yaml.safe_load(WATCH_PATH.read_text()) or {}
    return data, data.get("companies") or []


def matches(name: str, wanted: list[str] | None) -> bool:
    """--companies: a name matches if any wanted name is part of it, ignoring case ("deloitte" finds "Deloitte UK")."""
    return not wanted or any(w.lower() in name.lower() for w in wanted)


def _csv_path(data: dict) -> Path:
    return ROOT / (data.get("output_file") or "output/watch_jobs_available.csv")


def add_rows(path: Path, new_rows: list[dict]) -> int:
    rows = []
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    have = {r["Link"] for r in rows}
    add = [r for r in new_rows if r["Link"] not in have]
    rows = sorted(add + rows, key=lambda r: (r["Date"], r.get("Posted") or ""), reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)
    return len(add)


def notify(n: int) -> None:
    msg = f"New jobs at watched companies: {n}"
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Job agent"'], check=False)


def screen(job, cfg: dict) -> None:
    """Skip rules on the full listing. Sets job.status and job.reason when one applies."""
    reason = rules.skip_reason(job, cfg) or rules.below_min_salary(job, cfg)
    floor = cfg["search"].get("min_salary") or 0
    if not reason and job.salary_max and job.salary_max < floor:
        reason = f"salary below minimum: listing says up to £{job.salary_max:,.0f}"
    if not reason and getattr(job, "_visa", "") == "No":
        reason = "no sponsorship: the listing says visa sponsorship is not available"
    if not reason and (m := NATIONALITY.search(job.description)):
        reason = f"UK nationality only vetting: \"{m.group(0)}\""
    if reason:
        job.status, job.reason = "skipped", reason


def run(cfg: dict, wanted: list[str] | None = None) -> dict:
    data, companies = load()
    settings = llm_settings(cfg)
    calls_before = dict(llm.CALLS)
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    out = run_dir() / "watch"
    s = bigfour.session()
    print("\nWatch list (never applies)...")

    todo = [c for c in companies if matches(c["name"], wanted)]
    if not companies:
        print("  config/watch_companies.yaml has no companies. Add the companies you want to watch.")
    if wanted and not todo:
        print(f"  no company in watch_companies.yaml matches {wanted}")
    stats, alerts, jobs = {}, [], []
    readable = [c for c in todo if c["name"] in bigfour.READERS]
    alerts += [{"company": c["name"], "why": "not set up yet: set up a job alert"} for c in todo if c["name"] not in bigfour.READERS]
    for n, c in enumerate(readable, 1):
        if progress.stop_requested():
            break
        name = c["name"]
        progress.say(f"Checking {name}, {n} of {len(readable)} companies")
        known = seen.setdefault(name, {})
        st = stats[name] = {"open": 0, "new": 0, "target": 0, "claude": 0, "matches": 0}
        try:
            listed = bigfour.READERS[name](s)
        except bigfour.Blocked as e:
            alerts.append({"company": name, "why": f"{e}: set up a job alert"})
            print(f"  {name}: {e}; set up a job alert")
            continue
        except Exception as e:      # a site change should not stop the other companies
            alerts.append({"company": name, "why": f"failed ({e.__class__.__name__}): set up a job alert"})
            print(f"  {name}: failed ({e.__class__.__name__}: {e}); set up a job alert")
            continue
        st["open"] = len(listed)
        fresh = [j for j in listed if j.job_id not in known]
        st["new"] = len(fresh)
        print(f"  {name}: {len(listed)} open, {len(fresh)} not seen before")
        for i, job in enumerate(fresh, 1):
            job._company = name
            jobs.append(job)
            if progress.stop_requested():
                job.status, job.reason = "deferred", progress.STOPPED
                continue
            if (r := rules.title_excluded(job, cfg)) or (not scoring.needs_intro(job.title) and not scoring.is_target_title(job.title)):
                job.status, job.reason = "skipped", r or OFF_TARGET
                continue
            st["target"] += 1
            progress.say(f"Reading {name} job listings, {i} of {len(fresh)}")
            try:
                job._detail()
            except bigfour.Blocked as e:
                alerts.append({"company": name, "why": f"{e}: set up a job alert"})
                print(f"  {name}: {e}; stopping this company, set up a job alert")
                job.status, job.reason = "deferred", "blocked"
                break
            except bigfour.Closed as e:
                job.status, job.reason = "skipped", str(e)
                continue
            except Exception as e:
                job.status, job.reason = "deferred", f"page did not load: {e.__class__.__name__}"
                continue
            if not scoring.is_target_title(job.title, job.description):
                job.status, job.reason = "skipped", OFF_TARGET
                continue
            if getattr(job, "_uk", True) is False:
                job.status, job.reason = "skipped", "not in the UK"
                continue
            screen(job, cfg)
            if job.status == "skipped":
                continue
            scoring.score(job)
            job.rules_score, job.scored_by = job.score, "rules"
            job.base_cv = scoring.base_cv_for(job.title)

    cands = sorted([j for j in jobs if j.status == "new" and j.score >= settings["score_from"]], key=lambda j: -j.score)
    held = []
    if settings["max_scores"] and len(cands) > settings["max_scores"]:
        held, cands = cands[settings["max_scores"]:], cands[:settings["max_scores"]]
    print(f"  {len(cands)} jobs scored {settings['score_from']}+ on the rules; scoring with Claude ({settings['score_model']})...")
    claude_score.score_all(cands, cfg)
    for job in cands:
        stats[job._company]["claude"] += 1
    for job in jobs:
        scoring.check_line_management(job)
        if job.status != "new" or job in held:
            continue
        limit = scoring.threshold_for(job.title, cfg)
        job.status, job.reason = ("eligible", "") if job.score >= limit else ("skipped", f"match score {job.score} below threshold {limit}")

    to_review = [review.entry(j, today().isoformat()) for j in jobs if j.reason.startswith("missing essential") and j.missing_essentials]
    added_review = review.add(to_review, cfg) if to_review else 0

    eligible = sorted([j for j in jobs if j.status == "eligible"], key=lambda j: -j.score)
    tailor_now = eligible[:settings["max_tailor"]] if settings["max_tailor"] else eligible
    new_rows = []
    if tailor_now:
        out.mkdir(parents=True, exist_ok=True)
        print(f"  tailoring {len(tailor_now)} CVs (Word only; run.py --pdf JOB_ID makes the PDF)...")
    for n, job in enumerate(tailor_now, 1):
        if progress.stop_requested():
            break
        progress.say(f"Writing CVs, {n} of {len(tailor_now)}")
        result = {"ok": False, "errors": []}
        for tries in range(2):
            try:
                result = tailor.tailor(job, cfg, out, keep="docx")
                break
            except Exception as e:      # the PDF for the page count did not come out: retry once (Claude's draft is cached)
                result = {"ok": False, "errors": [f"{e.__class__.__name__}: {e}"]}
                if "did not produce" in str(e) and tries == 0:
                    tailor._restart_word()
                    continue
                break
        print(f"  {'ok  ' if result['ok'] else 'FAIL'} {job.title} | {job._company} ({job.score})")
        if not result["ok"] and any("did not produce" in e for e in result["errors"]):
            continue                    # not marked seen: the next run tries again
        stats[job._company]["matches"] += 1
        notes = [bigfour.stretch(job._grade), "stretch: software engineering role" if scoring.is_software_role(job.title) else "",
                 "" if result["ok"] else "CV failed the CV rules checks"]
        new_rows.append({"Date": today().isoformat(), "Posted": job.posted.isoformat() if job.posted else "",
                         "Company": job._company, "Job title": job.title, "Grade": job._grade, "Location": job.location,
                         "Score": job.score, "Link": job.link,
                         "CV": str(Path(result["cv"]).resolve().relative_to(ROOT)) if result.get("cv") else "",
                         "Applied": "", "Note": "; ".join(x for x in notes if x)})
        seen[job._company][job.job_id] = today().isoformat()
    for job in jobs:
        if job.status in FINAL and job.status != "eligible":
            seen[job._company][job.job_id] = today().isoformat()

    added = add_rows(_csv_path(data), new_rows)
    SEEN.write_text(json.dumps(seen, indent=1))
    if added:
        notify(added)
    calls = {k: llm.CALLS[k] - calls_before.get(k, 0) for k in llm.CALLS}
    for a in alerts:
        print(f"  {a['company']}: {a['why']}")
    print(f"\nWatch list: {added} new matches -> {_csv_path(data).relative_to(ROOT)}")
    print(f"Missing essentials review (watch list): {len(to_review)} this run, {added_review} new on the list")
    print(f"Claude calls (watch list): {calls['made']} made, {calls['cached']} answered from the cache")
    return {"stats": stats, "alerts": alerts, "new": new_rows, "calls": calls, "review": len(to_review)}
