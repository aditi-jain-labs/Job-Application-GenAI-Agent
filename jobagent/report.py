"""Run state for the day, tailoring.md and report.md (CLAUDE.md section 6).

output/YYYY-MM-DD/state.json keeps every tailoring and application result for the day, keyed by job.
Runs merge into it, so `--only` updates the reports instead of replacing them.
"""
import json
from pathlib import Path

from .models import Job
from .settings import today

TAILOR_KEYS = ["base_cv", "cv", "cover_letter", "ok", "errors", "warnings", "addressee", "attempts", "pages",
               "qualification_map", "letter_text"]
JOB_KEYS = ["source", "job_id", "title", "company", "employer", "score", "link"]


def md(text) -> str:
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ")


def load_state(out: Path) -> dict:
    path = out / "state.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    return {"tailoring": state.get("tailoring", {}), "apply": state.get("apply", {}), "profile_cv": state.get("profile_cv", "")}


def save_state(out: Path, state: dict) -> None:
    (out / "state.json").write_text(json.dumps(state, indent=1, default=str))


def tailoring_record(result: dict) -> dict:
    job: Job = result["job"]
    rec = {k: (str(result[k]) if isinstance(result.get(k), Path) else result.get(k)) for k in TAILOR_KEYS}
    rec["job"] = {k: getattr(job, k) for k in JOB_KEYS}
    return rec


def addressed(rec: dict) -> str:
    a = rec.get("addressee")
    if not a:
        return ""
    kind = {"hiring_manager": "hiring manager", "recruiter_or_contact": "recruiter or contact"}.get(a["kind"])
    return f"{a['name']} ({kind})" if kind and a["name"] else "Dear Hiring Manager (no name in listing)"


def _link(path: str | None, text: str) -> str:
    return f"[{text}]({Path(path).name})" if path and Path(path).exists() else ""


def write_tailoring(out: Path, state: dict, letters: bool) -> None:
    recs = sorted(state["tailoring"].values(), key=lambda r: -(r["job"]["score"] or 0))
    head = ["Status", "Score", "Job title", "Employer", "Base CV"] + (["Addressed to"] if letters else []) + ["Pages", "Attempts", "Files"]
    lines = [f"# Tailoring {today().isoformat()}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in recs:
        j = r["job"]
        files = [_link(r.get("cv") and str(Path(r["cv"]).with_suffix(".pdf")), "CV")]
        if letters:
            files.append(_link(r.get("cover_letter") and str(Path(r["cover_letter"]).with_suffix(".pdf")), "Cover letter"))
        cells = [("ready" if r["ok"] else "needs fixing"), j["score"], md(j["title"]), md(j["employer"] or j["company"]),
                 r.get("base_cv") or ""] + ([md(addressed(r))] if letters else []) + \
                [r.get("pages") or "", r.get("attempts") or "", ", ".join(x for x in files if x)]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    for r in recs:
        j = r["job"]
        lines += ["", f"## {j['title']}, {j['employer'] or j['company']}", "", j["link"], ""]
        if letters and (a := r.get("addressee")):
            lines += [f"Addressed to: {addressed(r)}" + (f", from the listing: \"{a['source']}\"" if a.get("source") else ""), ""]
        if r.get("errors"):
            lines += ["**Rule violations still present (do not apply with these files):**", ""]
            lines += [f"- {e}" for e in r["errors"]] + [""]
        if r.get("warnings"):
            lines += ["**For your review:**", ""] + [f"- {w}" for w in r["warnings"]] + [""]
        if r.get("qualification_map"):
            lines += ["| Minimum qualification | Where the CV shows it |", "|---|---|"]
            lines += [f"| {md(q['qualification'])} | {md(q['shown_in'])} |" for q in r["qualification_map"]]
    (out / "tailoring.md").write_text("\n".join(lines) + "\n")


def outcome(job: Job, state: dict) -> tuple[str, str]:
    """(status, reason) for one job this run, from the furthest step it reached."""
    if job.status == "duplicate":
        return job.status, job.reason
    a = state["apply"].get(job.key)
    if a:
        return a["status"], a["reason"]
    t = state["tailoring"].get(job.key)
    if t and not t["ok"]:
        return "skipped", "CV failed the CLAUDE.md checks: " + "; ".join(t["errors"][:3])
    if job.status == "eligible":
        return ("deferred", "tailored, not applied this run") if t else ("deferred", "above threshold, not tailored this run")
    return job.status, job.reason


def write_report(out: Path, jobs: list[Job], state: dict, dry_run: bool) -> None:
    done, not_done = [], []
    for job in jobs:
        status, reason = outcome(job, state)
        (done if status in ("applied", "dry_run") else not_done).append((job, status, reason))
    order = lambda item: (item[0].key not in state["tailoring"], -(item[0].score or -1))
    done.sort(key=order)
    not_done.sort(key=order)

    def cv(job: Job) -> str:
        t = state["tailoring"].get(job.key) or {}
        return _link(t.get("cv") and str(Path(t["cv"]).with_suffix(".pdf")), "CV")

    title = "Ready to submit (dry run, nothing was submitted)" if dry_run else "Applied"
    lines = [f"# Report {today().isoformat()}{' (dry run)' if dry_run else ''}", "",
             f"## {title}", "",
             "| Job title | Company | Match score | Link | Base CV | CV | CV shown in the form | Screenshot |",
             "|---|---|---|---|---|---|---|---|"]
    for job, status, reason in done:
        t = state["tailoring"].get(job.key) or {}
        a = state["apply"].get(job.key) or {}
        lines.append(f"| {md(job.title)} | {md(job.employer or job.company)} | {job.score} | {job.link} | {t.get('base_cv', '')} | "
                     f"{cv(job)} | {md(a.get('cv_shown', ''))} | {_link(a.get('screenshot'), 'Screenshot')} |")
    lines += ["", "## Not applied", "",
              "| Job title | Company | Match score | Link | Reason | CV |", "|---|---|---|---|---|---|"]
    for job, status, reason in not_done:
        lines.append(f"| {md(job.title)} | {md(job.employer or job.company)} | {md(job.score)} | {job.link} | "
                     f"{md(reason)} | {cv(job)} |")
    if state.get("profile_cv"):
        lines += ["", "## Reed profile CV", "", state["profile_cv"]]

    notes = [(job, state["apply"][job.key]) for job, _, _ in done + not_done
             if job.key in state["apply"] and state["apply"][job.key].get("notes")]
    if notes:
        lines += ["", "## What the agent did on each form", ""]
        for job, a in notes:
            lines += [f"### {job.title}, {job.employer or job.company}", ""] + [f"- {n}" for n in a["notes"]] + [""]
    (out / "report.md").write_text("\n".join(lines) + "\n")
