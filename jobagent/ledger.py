"""applied.csv and cache/seen_jobs.csv.

applied.csv holds real applications only, one row per job: applied (submitted, or applied by hand),
unconfirmed (Submit clicked, no success message seen; blocks the job so it is never sent twice) and
rejected. Every other outcome (skipped, duplicate, deferred, dry_run) goes to cache/seen_jobs.csv with
job_id, source, status, reason and date. Rows from a dry run carry the status "<status> (dry run)",
so nothing a dry run records ever blocks a real run. A job is "already seen" if it is in applied.csv,
or in seen_jobs.csv as skipped or duplicate. Deferred jobs are looked at again.
"""
import csv
import datetime as dt
import json
import re

from .models import Job
from .settings import APPLIED_CSV, CACHE_DIR, OUTPUT_DIR

FIELDS = ["job_id", "source", "title", "company", "employer", "limit_key", "match_score", "link",
          "status", "reason", "mode", "date", "time"]
SEEN_CSV = CACHE_DIR / "seen_jobs.csv"
SEEN_FIELDS = ["job_id", "source", "status", "reason", "date"]
APPLIED = {"applied", "unconfirmed", "rejected"}     # statuses kept in applied.csv
BLOCKING = {"skipped", "duplicate"}                  # seen_jobs.csv statuses that stop a job being looked at again


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def company_title(company: str, title: str) -> str:
    return f"{norm(company)}|{norm(title)}"


def rows() -> list[dict]:
    if not APPLIED_CSV.exists():
        return []
    with open(APPLIED_CSV, newline="") as f:
        return [{**{k: "" for k in FIELDS}, **r} for r in csv.DictReader(f)]


def seen_rows() -> list[dict]:
    if not SEEN_CSV.exists():
        return []
    with open(SEEN_CSV, newline="") as f:
        return list(csv.DictReader(f))


def load_seen() -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """Return (source:job_id keys, links, (company, title) pairs) that block a job from being seen again."""
    keys, links, pairs = set(), set(), set()
    for row in rows():
        if (row["mode"] or "live") != "live":
            continue
        keys.add(f"{row['source']}:{row['job_id']}")
        if row["link"]:
            links.add(row["link"])
        pairs.add((row["company"], row["title"]))
    keys |= {f"{r['source']}:{r['job_id']}" for r in seen_rows() if r["status"] in BLOCKING}
    return keys, links, pairs


def live_applications() -> list[tuple[dt.datetime, str]]:
    """(when, limit_key) for every real application, for the daily, hourly and agency limits."""
    out = []
    for row in rows():
        if row["status"] == "applied" and (row["mode"] or "live") == "live" and row["date"]:
            when = dt.datetime.fromisoformat(f"{row['date']}T{row['time'] or '00:00'}")
            out.append((when, row["limit_key"] or row["company"]))
    return out


def write_applied(all_rows: list[dict]) -> None:
    """Rewrite applied.csv with one row per job (the latest)."""
    latest = {}
    for r in all_rows:
        latest[(r["source"], r["job_id"])] = r
    with open(APPLIED_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in latest.values())


def add_seen(seen: list[dict]) -> None:
    new = not SEEN_CSV.exists() or not SEEN_CSV.stat().st_size
    SEEN_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SEEN_FIELDS)
        if new:
            w.writeheader()
        w.writerows({k: r.get(k, "") for k in SEEN_FIELDS} for r in seen)


def append(entries: list[dict]) -> None:
    """entries: dicts with a Job under "job" plus status, reason and mode."""
    now = dt.datetime.now()
    applied, seen = [], []
    for e in entries:
        j: Job = e["job"]
        row = {
            "job_id": j.job_id, "source": j.source, "title": j.title, "company": j.company,
            "employer": j.employer, "limit_key": j.limit_key,
            "match_score": "" if j.score is None else j.score, "link": j.link,
            "status": e["status"], "reason": e.get("reason", ""), "mode": e["mode"],
            "date": now.date().isoformat(), "time": now.strftime("%H:%M"),
        }
        if e["status"] in APPLIED and e["mode"] == "live":
            applied.append(row)
        else:
            if e["mode"] != "live" and e["status"] != "dry_run":
                row["status"] = f"{e['status']} (dry run)"
            seen.append(row)
    if applied:
        write_applied(rows() + applied)
    if seen:
        add_seen(seen)


def mark_applied(job_id: str) -> str:
    """Record a job you applied to by hand. Looks the job up in applied.csv, then saved screenings, then seen_jobs.csv."""
    known = next((r for r in reversed(rows()) if r["job_id"] == job_id), None)
    found, listed = None, None
    if not known:
        from . import byhand
        listed = byhand.find(job_id)          # referral, watch list, manual or apply-by-hand job
    if not known and not listed:
        for path in sorted(OUTPUT_DIR.glob("*/screening.json"), reverse=True):
            found = next((d for d in json.loads(path.read_text()) if d["job_id"] == job_id), None)
            if found:
                break
    if known:
        job = Job(source=known["source"], job_id=job_id, title=known["title"], company=known["company"],
                  location="", link=known["link"], posted=None, employer=known["employer"])
        job.score = int(known["match_score"]) if known["match_score"] else None
        job.via_job_board = known["limit_key"] != known["company"]
    elif listed:
        job = Job(source=listed.get("source") or listed["kind"], job_id=str(listed["job_id"]), title=listed["title"],
                  company=listed["company"], location="", link=listed["link"], posted=None)
        job.score = int(listed["score"]) if str(listed.get("score") or "").isdigit() else None
    elif found:
        job = Job(**{**{k: v for k, v in found.items() if k in Job.__dataclass_fields__}, "posted": None, "expires": None})
    else:
        seen = next((r for r in reversed(seen_rows()) if r["job_id"] == job_id), None)
        if not seen:
            raise SystemExit(f"job {job_id} is not in applied.csv, seen_jobs.csv or any output/*/screening.json")
        job = Job(source=seen["source"], job_id=job_id, title="", company="", location="", link="", posted=None)
    was = " (was unconfirmed)" if known and known["status"] == "unconfirmed" else ""
    append([{"job": job, "status": "applied", "reason": "applied by hand" + was, "mode": "live"}])
    return f"{job.title} | {job.employer or job.company}"


def unmark_applied(job_id: str) -> bool:
    """Undo a by-hand record (dashboard: Applied ticked by mistake). Rows the agent submitted are never removed;
    a job that was unconfirmed goes back to unconfirmed, so it still can't be sent twice."""
    out, changed = [], False
    for r in rows():
        if r["job_id"] == job_id and r["reason"].startswith("applied by hand"):
            changed = True
            if r["reason"].endswith("(was unconfirmed)"):
                out.append({**r, "status": "unconfirmed", "reason": "submit not confirmed (Applied unticked)"})
            continue
        out.append(r)
    if changed:
        write_applied(out)
    return changed
