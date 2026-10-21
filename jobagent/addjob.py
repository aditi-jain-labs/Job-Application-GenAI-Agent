"""run.py --job: score one job you found yourself and write a tailored CV for it.

The job is a URL (a Reed link uses the Reed API; any other page is read as text) or a text file with the
description. It goes through the same skip rules and Claude scoring as every other job (CLAUDE.md section 2).
If nothing rules it out, the CV is written to output/manual/ as a Word file (run.py --pdf JOB_ID makes the PDF)
and the job is added to output/manual_jobs.csv. Pages with a bot check or a login are not worked around:
paste the description instead.
"""
import csv
import hashlib
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import claude_score, details, llm, review, rules, scoring, tailor
from .apply import BOT_TEXT
from .models import Job
from .settings import OUTPUT_DIR, env, llm_settings, load_config, today

MANUAL_DIR = OUTPUT_DIR / "manual"
MANUAL_CSV = OUTPUT_DIR / "manual_jobs.csv"
FIELDS = ["Date", "Company", "Job title", "Link", "Score", "CV", "Applied", "Job id", "Note"]
REED_ID = re.compile(r"reed\.co\.uk/jobs/[^/]+/(\d+)")


def _reed(url: str, job_id: str) -> Job:
    r = requests.get(details.REED_DETAILS.format(id=job_id), auth=(env("REED_API_KEY"), ""), timeout=30)
    if r.status_code == 404:
        raise SystemExit("Reed says this job does not exist any more (404)")
    r.raise_for_status()
    d = r.json()
    job = Job(source="reed", job_id=job_id, title=d.get("jobTitle", ""), company=d.get("employerName", ""),
              location=d.get("locationName", ""), link=url.split("?")[0], posted=None,
              salary_min=d.get("minimumSalary"), salary_max=d.get("maximumSalary"),
              description=details.html_to_text(d.get("jobDescription") or ""), apply_url=d.get("externalUrl") or "",
              contract_type=d.get("contractType") or "")
    return job


def _page(url: str) -> Job:
    try:
        r = requests.get(url, headers={"User-Agent": details.BROWSER_UA}, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        raise SystemExit(f"could not load the page ({e.__class__.__name__}); paste the description instead")
    if r.status_code in (401, 403, 429) or BOT_TEXT.search(r.text[:20000]):
        raise SystemExit("the page has a bot check or needs a login, which the agent does not get past; paste the description instead")
    if r.status_code == 404:
        raise SystemExit("page not found (404)")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    og = soup.find("meta", property="og:title")
    title = (og.get("content") if og else "") or (soup.title.get_text() if soup.title else "")
    site = soup.find("meta", property="og:site_name")
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return Job(source="manual", job_id="", title=title.strip(), company=(site.get("content") if site else "").strip(),
               location="", link=url, posted=None, description=details.html_to_text(str(main)))


def load(source: str, title: str | None, company: str | None) -> Job:
    if re.match(r"https?://", source):
        m = REED_ID.search(source)
        job = _reed(source, m.group(1)) if m else _page(source)
    else:
        if not Path(source).expanduser().is_file():
            raise SystemExit(f"{source} is not a URL or a file")
        text = Path(source).expanduser().read_text().strip()
        m = re.match(r"Link:\s*(https?://\S+)\s*", text)          # the dashboard puts the optional link first
        job = Job(source="manual", job_id="", title="", company="", location="", link=m.group(1) if m else "", posted=None,
                  description=text[m.end():] if m else text)
    job.title = title or job.title
    job.company = company or job.company
    if not job.title:
        raise SystemExit("no job title found: add --title")
    if len(job.description) < 200:
        raise SystemExit("the description is too short to score (under 200 characters); paste the full listing")
    if not job.job_id:
        job.job_id = "manual-" + hashlib.sha1((job.link or job.description).encode()).hexdigest()[:8]
    return job


def _add_row(job: Job, cv: Path | None, note: str) -> None:
    rows = []
    if MANUAL_CSV.exists():
        with open(MANUAL_CSV, encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("Job id") != job.job_id]
    rows.insert(0, {"Date": today().isoformat(), "Company": job.employer or job.company, "Job title": job.title,
                    "Link": job.link, "Score": job.score, "CV": str(cv.relative_to(OUTPUT_DIR.parent)) if cv else "",
                    "Applied": "", "Job id": job.job_id, "Note": note})
    with open(MANUAL_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)


def run(source: str, title: str | None = None, company: str | None = None) -> dict:
    cfg = load_config()
    llm.USE_CACHE = llm_settings(cfg)["cache"]
    job = load(source, title, company)
    print(f"Job: {job.title} | {job.company or '(company not found)'} | {job.job_id}")

    reason = rules.skip_reason(job, cfg) or rules.below_min_salary(job, cfg)
    if not reason:
        scoring.score(job)
        job.rules_score, job.scored_by = job.score, "rules"
        claude_score.score_all([job], cfg)         # always: you picked this job yourself
        scoring.check_line_management(job)
        reason = job.reason if job.status == "skipped" else ""
    if job.missing_essentials:
        review.add([review.entry(job, today().isoformat())], cfg)
    print(f"Score: {job.score}" + (f" ({job.score_summary})" if job.score_summary else ""))
    for m in job.missing_essentials:
        print(f"  missing essential: {m['requirement']}")
    if reason:
        print(f"Not tailored: {reason}")
        _add_row(job, None, f"not tailored: {reason}")
        return {"job": job, "cv": None, "reason": reason}

    threshold = scoring.threshold_for(job.title, cfg)
    note = "" if job.score >= threshold else f"score {job.score} is below the threshold {threshold}; CV written because you added it"
    MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    result = tailor.tailor(job, cfg, MANUAL_DIR, keep="docx")
    if not result["ok"]:
        note = "; ".join(filter(None, [note, "CV failed the CLAUDE.md checks: " + "; ".join(result["errors"][:2])]))
    _add_row(job, Path(result["cv"]), note)
    print(f"CV: {result['cv']}" + (f"\nNote: {note}" if note else ""))
    print(f"Make the PDF with: python3 run.py --pdf {job.job_id}")
    print(f"Claude calls: {llm.CALLS['made']} made, {llm.CALLS['cached']} answered from the cache")
    return {"job": job, "cv": result["cv"], "reason": note}
