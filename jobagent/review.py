"""Missing essentials review list (cv_rules_for_agent.md section 2, config.yaml review.missing_essentials_file).

Jobs with a target title that were skipped for a missing essential, newest first, one row per job,
so you can check whether you really lack the requirement or just haven't added it to profile.md.
The rows live in cache/missing_essentials.json; the markdown file is rebuilt from them.
"""
import json
import re

from .models import Job
from .settings import CACHE_DIR, ROOT

STORE = CACHE_DIR / "missing_essentials.json"
STOP = {"and", "the", "with", "for", "of", "in", "on", "or", "to", "a", "an", "experience", "strong", "proven",
        "knowledge", "understanding", "working", "hands", "including", "such", "similar", "e.g", "etc", "one"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]{3,}", text.lower()) if w not in STOP}


def best_line(requirement: str, description: str) -> str:
    """The listing line that shares the most words with the requirement."""
    lines = [ln.strip(" •-*\t") for ln in re.split(r"\n+|(?<=[.;])\s+(?=[A-Z])", description) if len(ln.strip()) > 8]
    want = _words(requirement)
    best = max(lines, key=lambda ln: len(want & _words(ln)), default="")
    return best[:300] if want & _words(best) else ""


def quote_for(requirement: str, claude_quote: str, description: str) -> str:
    """Claude's quote if it really is in the listing, otherwise the closest line."""
    norm = lambda t: re.sub(r"\s+", " ", t).strip().lower()
    if claude_quote and norm(claude_quote) in norm(description):
        return claude_quote.strip()
    return best_line(requirement, description)


def _path(cfg: dict):
    return ROOT / ((cfg.get("review") or {}).get("missing_essentials_file") or "output/missing_essentials.md")


def _md(text) -> str:
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ")


def add(entries: list[dict], cfg: dict) -> int:
    """entries: {date, job_id, title, company, score, link, missing: [{requirement, quote}]}. Returns how many were new."""
    rows = json.loads(STORE.read_text()) if STORE.exists() else []
    seen = {r["job_id"] for r in rows}
    new = [e for e in entries if e["job_id"] not in seen]
    rows = sorted(new + rows, key=lambda r: r["date"], reverse=True)      # stable: newer runs first
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows, indent=1))

    lines = ["# Missing essentials to review", "",
             "Target roles skipped for a missing essential, newest first. If you do have the skill, add it to "
             "profile.md and the job will be scored again on the next run that sees it.", "",
             "| Date | Job title | Company | Score | Link | Missing requirement | Line from the listing |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        req = "<br>".join(_md(m["requirement"]) for m in r["missing"])
        quotes = "<br>".join(f"\"{_md(m['quote'])}\"" if m["quote"] else "(no matching line found)" for m in r["missing"])
        lines.append(f"| {r['date']} | {_md(r['title'])} | {_md(r['company'])} | {r['score']} | {r['link']} | {req} | {quotes} |")
    path = _path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return len(new)


def entry(job: Job, date: str) -> dict:
    return {"date": date, "job_id": job.job_id, "title": job.title, "company": job.employer or job.company,
            "score": job.score, "link": job.link, "missing": job.missing_essentials}
