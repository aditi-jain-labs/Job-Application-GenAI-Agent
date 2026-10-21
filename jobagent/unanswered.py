"""output/unanswered_questions.md: required form questions answers.yaml does not cover, from Reed forms and
company boards (ATS), most common first. Adding an answer to answers.yaml unlocks those jobs next time.
The list lives in cache/unanswered_questions.json.
"""
import json
import re
from collections import defaultdict

from .models import Job
from .settings import CACHE_DIR, OUTPUT_DIR, today

STORE = CACHE_DIR / "unanswered_questions.json"
PAGE = OUTPUT_DIR / "unanswered_questions.md"
# Reed skip reasons that name a question (apply.fill_form)
REED_QUESTION = re.compile(r"^(question not covered in answers\.yaml: |answers\.yaml has no (yes or no )?answer (that fits|for checkbox) )", re.I)


def _md(text) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ")


def from_reed_reason(reason: str) -> str:
    """The question in a Reed skip reason, or "" if the reason is not about a question."""
    if not REED_QUESTION.search(reason or ""):
        return ""
    m = re.search(r"\"([^\"]+)\"", reason)
    return m.group(1) if m else REED_QUESTION.sub("", reason)


def add(job: Job, questions: list[dict]) -> None:
    """questions: [{label, note}]."""
    rows = json.loads(STORE.read_text()) if STORE.exists() else []
    have = {(r["job"], r["question"]) for r in rows}
    key = f"{job.source}:{job.job_id}"
    for q in questions:
        label = re.sub(r"\s+", " ", q["label"]).strip()
        if (key, label) in have:
            continue
        rows.append({"date": today().isoformat(), "job": key, "title": job.title, "company": job.employer or job.company,
                     "link": job.link, "question": label, "note": q.get("note", "")})
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows, indent=1))
    write()


def write() -> None:
    rows = json.loads(STORE.read_text()) if STORE.exists() else []
    groups = defaultdict(list)
    for r in rows:
        groups[r["question"].lower()].append(r)
    lines = ["# Unanswered form questions", "",
             "Required questions on application forms that answers.yaml does not cover. Each one stopped the agent "
             "applying. Add an answer to answers.yaml (only if it is true) and these jobs can be applied to automatically.", "",
             "| Question | Jobs | Why | Last seen | Example job |", "|---|---|---|---|---|"]
    for _, rs in sorted(groups.items(), key=lambda g: (-len(g[1]), g[0])):
        last = max(rs, key=lambda r: r["date"])
        notes = "; ".join(dict.fromkeys(r["note"] for r in rs if r["note"]))
        lines.append(f"| {_md(rs[0]['question'][:200])} | {len(rs)} | {_md(notes)} | {last['date']} | "
                     f"[{_md(last['title'])} at {_md(last['company'])}]({last['link']}) |")
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text("\n".join(lines) + "\n")
