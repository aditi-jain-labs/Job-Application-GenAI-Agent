"""Jobs to apply to by hand, and marking them as applied.

cache/apply_by_hand.json keeps Reed and company board (ATS) jobs that have a tailored CV but that the agent
could not send itself: external sites, login or bot checks, questions answers.yaml does not cover, no submit
button. Referral, watch list and manual jobs live in their own files; all of them are listed together by
everything() for the dashboard.

mark_done(job_id) is called by run.py --mark-applied after the applied.csv row is written: it fills the
Applied column of referral_jobs_available.csv, watch_jobs_available.csv or manual_jobs.csv, and the
applied date in the queue.
"""
import csv
import json
import re
from pathlib import Path

import yaml

from .models import Job
from .settings import CACHE_DIR, OUTPUT_DIR, ROOT, WATCH_PATH, today

QUEUE = CACHE_DIR / "apply_by_hand.json"
REFERRAL_JSON = CACHE_DIR / "referral_jobs.json"
REFERRAL_CSV = OUTPUT_DIR / "referral_jobs_available.csv"
MANUAL_CSV = OUTPUT_DIR / "manual_jobs.csv"
REFERRAL_FIELDS = ["Date", "Company", "Job title", "Location", "Score", "Link", "CV", "Referral asked", "Applied", "Note"]
ADDED = {"count": 0}            # queue entries added this run, for the run summary


def watch_csv() -> Path:
    path = WATCH_PATH
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return ROOT / ((data or {}).get("output_file") or "output/watch_jobs_available.csv")


def watch_id(link: str) -> str:
    """The job id jobagent/bigfour.py gives a watch list job, from its link (the CSV has no id column)."""
    for pattern, prefix in ((r"jobs\.pwc\.co\.uk/.*/job/([^/]+)/", "pwc"), (r"apply\.deloitte\.co\.uk/.*/JobDetail/[^/]+/(\d+)", "deloitte"),
                            (r"kpmgcareers\.co\.uk/Vacancies/[^/]+/([^/?]+)", "kpmg")):
        if m := re.search(pattern, link or "", re.I):
            return f"{prefix}-{m.group(1)}"
    return link


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:      # keeps the BOM Excel wants
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def add(job: Job, cv: str, reason: str, kind: str = "external") -> None:
    """kind: "external" for Reed and company board jobs."""
    queue = _load(QUEUE, {})
    key = f"{job.source}:{job.job_id}"
    if key in queue and queue[key].get("applied"):
        return
    if key not in queue:
        ADDED["count"] += 1
    queue[key] = {"kind": kind, "source": job.source, "job_id": job.job_id, "title": job.title,
                  "company": job.employer or job.company, "score": job.score, "link": job.link,
                  "apply_url": job.apply_url, "cv": str(cv or ""), "reason": reason,
                  "date": queue.get(key, {}).get("date") or today().isoformat(), "applied": ""}
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(queue, indent=1))


def _id_in(job_id: str, text: str) -> bool:
    tail = re.split(r"[-:]", job_id)[-1]
    return bool(tail) and bool(re.search(rf"(?<![0-9A-Za-z]){re.escape(tail)}(?![0-9A-Za-z])", text or ""))


def everything() -> list[dict]:
    """One list for the dashboard: kind, job_id, company, title, score, link, cv, date, reason, applied."""
    out = []
    ref_csv = _read_csv(REFERRAL_CSV)
    for r in _load(REFERRAL_JSON, []):
        row = next((c for c in ref_csv if c.get("Link") == r["link"]), {})
        out.append({"kind": "referral", "job_id": r["job_id"], "company": r["company"], "title": r["title"],
                    "score": r["score"], "link": r["link"], "cv": r.get("cv", ""), "date": r["date"],
                    "reason": "ask for a referral first" + (f"; {row['Note']}" if row.get("Note") else ""),
                    "applied": r.get("applied") or ("yes" if (row.get("Applied") or "").strip() else ""),
                    "referral_asked": row.get("Referral asked", "")})
    for r in _read_csv(watch_csv()):
        out.append({"kind": "watch", "job_id": r.get("Job id") or r.get("job_id") or watch_id(r.get("Link", "")),
                    "company": r.get("Company", ""), "title": r.get("Job title", ""), "score": r.get("Score", ""),
                    "link": r.get("Link", ""), "cv": r.get("CV", ""), "date": r.get("Date", ""), "reason": "",
                    "applied": r.get("Applied", "")})
    for r in _read_csv(MANUAL_CSV):
        ids = re.findall(r"\d{5,}", r.get("Link", ""))
        out.append({"kind": "manual", "job_id": r.get("Job id") or (f"{r.get('Company', '').lower()}-{ids[-1]}" if ids else r.get("Link", "")),
                    "company": r.get("Company", ""), "title": r.get("Job title", ""), "score": r.get("Score", ""),
                    "link": r.get("Link", ""), "cv": r.get("CV", ""), "date": r.get("Date", ""), "reason": r.get("Note", ""),
                    "applied": r.get("Applied", "")})
    for r in _load(QUEUE, {}).values():
        out.append({**r, "link": r.get("apply_url") or r["link"]})
    return out


def find(job_id: str) -> dict | None:
    return next((r for r in everything() if str(r["job_id"]) == str(job_id)), None) or \
        next((r for r in everything() if _id_in(str(job_id), str(r["job_id"]))), None)


def mark_done(job_id: str) -> list[str]:
    """Fill the Applied column wherever the job is listed. Returns the files changed."""
    changed, stamp = [], today().isoformat()
    queue = _load(QUEUE, {})
    for r in queue.values():
        if str(r["job_id"]) == str(job_id):
            r["applied"] = stamp
            changed.append(str(QUEUE.relative_to(ROOT)))
    if changed:
        QUEUE.write_text(json.dumps(queue, indent=1))

    refs = _load(REFERRAL_JSON, [])
    ref = next((r for r in refs if r["job_id"] == job_id), None)
    if ref:
        ref["applied"] = stamp
        REFERRAL_JSON.write_text(json.dumps(refs, indent=1))
        rows = _read_csv(REFERRAL_CSV)
        fields = list(rows[0].keys()) if rows else REFERRAL_FIELDS
        row = next((r for r in rows if r.get("Link") == ref["link"]), None)
        if row is None:
            row = {k: "" for k in fields}
            row.update({"Date": ref["date"], "Company": ref["company"], "Job title": ref["title"], "Location": ref.get("location", ""),
                        "Score": ref["score"], "Link": ref["link"], "CV": ref.get("cv", "")})
            rows.insert(0, row)
        row["Applied"] = "Yes"
        _write_csv(REFERRAL_CSV, rows, fields)
        changed.append(str(REFERRAL_CSV.relative_to(ROOT)))

    for path in (watch_csv(), MANUAL_CSV):
        rows = _read_csv(path)
        hit = [r for r in rows if (r.get("Job id") or r.get("job_id")) == job_id
               or _id_in(job_id, r.get("Link", "") + " " + r.get("CV", ""))]
        if hit:
            for r in hit:
                r["Applied"] = "Yes"
            _write_csv(path, rows, list(rows[0].keys()))
            changed.append(str(path.relative_to(ROOT)))
    return changed


def set_referral_asked(link: str, asked: bool) -> None:
    rows = _read_csv(REFERRAL_CSV)
    for r in rows:
        if r.get("Link") == link:
            r["Referral asked"] = today().isoformat() if asked else ""
    if rows:
        _write_csv(REFERRAL_CSV, rows, list(rows[0].keys()))


def unmark_done(job_id: str) -> None:
    """Clear the Applied marks that mark_done set."""
    queue = _load(QUEUE, {})
    for r in queue.values():
        if str(r["job_id"]) == str(job_id):
            r["applied"] = ""
    if queue:
        QUEUE.write_text(json.dumps(queue, indent=1))
    refs = _load(REFERRAL_JSON, [])
    ref = next((r for r in refs if r["job_id"] == job_id), None)
    if ref:
        ref.pop("applied", None)
        REFERRAL_JSON.write_text(json.dumps(refs, indent=1))
    for path in (REFERRAL_CSV, watch_csv(), MANUAL_CSV):
        rows = _read_csv(path)
        hit = [r for r in rows if (ref and r.get("Link") == ref["link"]) or (r.get("Job id") or r.get("job_id")) == job_id
               or (not ref and _id_in(job_id, r.get("Link", "") + " " + r.get("CV", "")))]
        for r in hit:
            r["Applied"] = ""
        if hit:
            _write_csv(path, rows, list(rows[0].keys()))
