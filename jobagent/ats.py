"""Company job boards on Greenhouse, Lever and Ashby (ats_companies.yaml), through their free public feeds.

Same rules as the Reed run: UK only, target titles and exclude_title_words (before anything else),
permanent only, min salary, sponsorship, DV clearance, missing essentials (also added to the review list),
rule score, then Claude for jobs at llm.rule_score_min_for_llm or above, and match_threshold.

For each match it writes a tailored CV and reads the application form's questions from the job system
(no browser). A job can be auto-applied only when every required question is covered by answers.yaml
(cv_rules_for_agent.md section 1); otherwise it is listed for manual applying with the unanswered questions.
Applying itself is not built yet: a dry run reports what would happen, and a live run submits nothing.
"""
import datetime as dt
import html
import json
import re
from collections import Counter
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from . import byhand, claude_score, progress, details, ledger, llm, review, rules, scoring, tailor, unanswered
from .apply import RULES, _answer, _pick_option, load_answers
from .models import Job
from .settings import ATS_COMPANIES_PATH, ROOT, llm_settings, run_dir, today

COMPANIES_PATH = ATS_COMPANIES_PATH
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
OFF_TARGET = "title is not a target role"

UK = re.compile(r"\b(uk|u\.k\.|united\s+kingdom|great\s+britain|gbr?|england|scotland|wales|northern\s+ireland|london|bristol|"
                r"manchester|edinburgh|glasgow|cambridge|oxford|leeds|birmingham|belfast|cardiff|reading|bath|newcastle|"
                r"sheffield|nottingham|liverpool|brighton|southampton|milton\s+keynes)\b", re.I)
NOT_UK = re.compile(r"ontario|canada|kentucky|connecticut|new\s+england|cambridge,?\s*(ma|massachusetts)|new\s+london|"
                    r"london,?\s*(on|ky|oh)\b", re.I)
NOT_PERMANENT = re.compile(r"contract|temporary|temp\b|intern|fixed|freelance|part[\s-]?time|seasonal", re.I)
CONSENT = re.compile(r"privacy|consent|acknowledg|i\s+have\s+read|i\s+agree|i\s+confirm\s+that|terms|notetaker|note[\s-]+taker|"
                     r"data\s+protection|gdpr|keeping\s+your\s+data|candidate\s+notice|recording", re.I)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def _ms_date(ms) -> dt.date | None:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date() if ms else None


def _iso_date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _is_uk(*places: str) -> bool:
    text = " | ".join(p for p in places if p)
    return bool(UK.search(text)) and not (NOT_UK.search(text) and not re.search(r"united\s+kingdom|\buk\b", text, re.I))


def _job(ats: str, company: str, slug: str, job_id, title: str, location: str, link: str, posted, description: str,
         contract: str = "") -> Job:
    job = Job(source=ats, job_id=f"{slug}-{job_id}", title=" ".join(title.split()), company=company, location=location or "",
              link=link, posted=posted, description=description,
              contract_type=contract if NOT_PERMANENT.search(contract or "") else "")
    job._ats, job._slug, job._raw_id = ats, slug, str(job_id)
    return job


# ---------- job feeds ----------

def greenhouse(s, company, slug):
    data = s.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, timeout=60)
    data.raise_for_status()
    out = []
    for r in data.json()["jobs"]:
        places = [(r.get("location") or {}).get("name", "")] + [o.get("name", "") for o in r.get("offices") or []]
        if not _is_uk(*places):
            continue
        out.append(_job("greenhouse", company, slug, r["id"], r["title"], places[0], r["absolute_url"],
                        _iso_date(r.get("first_published") or r.get("updated_at")),
                        details.html_to_text(html.unescape(r.get("content") or ""))))     # content comes HTML-escaped
    return out


def lever(s, company, slug):
    data = s.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=90)
    data.raise_for_status()
    out = []
    for r in data.json():
        cat = r.get("categories") or {}
        if not (r.get("country") == "GB" or _is_uk(cat.get("location", ""), *(cat.get("allLocations") or []))):
            continue
        text = "\n".join([r.get("openingPlain") or "", r.get("descriptionBodyPlain") or r.get("descriptionPlain") or ""]
                         + [f"{l['text']}\n{details.html_to_text(l['content'])}" for l in r.get("lists") or []]
                         + [r.get("additionalPlain") or ""])
        out.append(_job("lever", company, slug, r["id"], r["text"], cat.get("location", ""), r["hostedUrl"],
                        _ms_date(r.get("createdAt")), text, cat.get("commitment", "")))
    return out


def ashby(s, company, slug):
    data = s.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"}, timeout=60)
    data.raise_for_status()
    out = []
    for r in data.json()["jobs"]:
        if r.get("isListed") is False:
            continue
        addr = ((r.get("address") or {}).get("postalAddress") or {})
        places = [r.get("location", ""), addr.get("addressCountry", "")] + [x.get("location", "") for x in r.get("secondaryLocations") or []]
        if not _is_uk(*places):
            continue
        comp = (r.get("compensation") or {}).get("compensationTierSummary") or ""
        text = (r.get("descriptionPlain") or details.html_to_text(r.get("descriptionHtml"))) + (f"\nSalary: {comp}" if comp else "")
        out.append(_job("ashby", company, slug, r["id"], r["title"], r.get("location", ""), r["jobUrl"],
                        _iso_date(r.get("publishedAt")), text, r.get("employmentType", "")))
    return out


FEEDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


# ---------- application questions ----------

def _gh_questions(s, job):
    d = s.get(f"https://boards-api.greenhouse.io/v1/boards/{job._slug}/jobs/{job._raw_id}", params={"questions": "true"}, timeout=60).json()
    qs = []
    for q in (d.get("questions") or []) + (d.get("location_questions") or []):
        f = (q.get("fields") or [{}])[0]
        qs.append({"label": q["label"], "required": q.get("required", False), "type": f.get("type", ""),
                   "options": [v.get("label", "") for v in f.get("values") or []]})
    for group in (d.get("compliance") or []):
        for q in group.get("questions") or []:
            f = (q.get("fields") or [{}])[0]
            qs.append({"label": q["label"], "required": q.get("required", False), "type": f.get("type", ""),
                       "options": [v.get("label", "") for v in f.get("values") or []]})
    demo = d.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        qs.append({"label": q["label"], "required": q.get("required", False), "type": q.get("type", ""),
                   "options": [a.get("label", "") for a in q.get("answer_options") or []]})
    return qs


ASHBY_FORM = ("query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { jobPosting("
              "organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { id "
              "applicationForm { sections { fieldEntries { ... on FormFieldEntry { id isRequired field } } } } } }")


def _ashby_questions(s, job):
    r = s.post("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", timeout=60,
               json={"operationName": "ApiJobPosting", "query": ASHBY_FORM,
                     "variables": {"organizationHostedJobsPageName": job._slug, "jobPostingId": job._raw_id}}).json()
    form = ((r.get("data") or {}).get("jobPosting") or {}).get("applicationForm") or {}
    qs = []
    for sec in form.get("sections") or []:
        for e in sec.get("fieldEntries") or []:
            f = e.get("field") or {}
            if f.get("isDeactivated"):
                continue
            qs.append({"label": f.get("title", ""), "required": e.get("isRequired", False), "type": f.get("type", ""),
                       "options": [v.get("label", "") for v in f.get("selectableValues") or []]})
    if not qs:
        raise ValueError("Ashby returned no application form")
    return qs


def _lever_questions(s, job):
    page = s.get(job.link.rstrip("/") + "/apply", timeout=60)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "html.parser")
    qs = []
    for li in soup.select("li.application-question"):
        lab = li.select_one(".application-label")
        label = re.sub(r"\s*✱\s*", " ", lab.get_text(" ", strip=True) if lab else "").strip()
        field = li.select_one("input, select, textarea")
        options = [o.get_text(strip=True) for o in li.select("option")] + \
                  [l.get_text(" ", strip=True) for l in li.select("label") if l.select_one("input[type=radio], input[type=checkbox]")]
        qs.append({"label": label, "required": bool(li.select_one(".required")) or "✱" in (lab.get_text() if lab else ""),
                   "type": (field.get("type") or field.name) if field else "", "options": options})
    if not qs:
        raise ValueError("no form found on the Lever apply page")
    return qs


QUESTIONS = {"greenhouse": _gh_questions, "lever": _lever_questions, "ashby": _ashby_questions}


def answer_for(q: dict, answers: dict, letters: bool) -> tuple[str | None, str]:
    """(answer, note) for one question from answers.yaml only. answer None means it is not covered."""
    label = q["label"]
    if CONSENT.search(label):
        return None, "consent or privacy acknowledgement (not in answers.yaml)"
    for rx, key in RULES:
        if not rx.search(label):
            continue
        if key == "CV":
            return "tailored CV", ""
        if key == "COVER":
            return (None, "cover letter (cover letters are off)") if not letters else ("cover letter", "")
        if key in ("FIRST", "LAST"):
            name = _answer(answers, "personal.full_name") or ""
            return (name.split()[0] if key == "FIRST" else name.split()[-1]) if name else None, ""
        value = _answer(answers, key)
        if value is None:
            return None, f"answers.yaml has no {key}"
        if q["options"]:
            pick = _pick_option(q["options"], value)
            return (pick, "") if pick else (None, f"no option fits answers.yaml {key} (\"{value}\")")
        return value, ""
    return None, "not covered by answers.yaml"


# ---------- the run ----------

def load_companies() -> tuple[dict, list[dict]]:
    data = yaml.safe_load(COMPANIES_PATH.read_text()) or {}
    return data.get("settings") or {}, data.get("companies") or []


MATCHES = ROOT / "cache" / "ats_matches.json"


def save_matches(results: dict) -> None:
    """cache/ats_matches.json: every company board match with its CV and outcome, for the dashboard."""
    rows = json.loads(MATCHES.read_text()) if MATCHES.exists() else {}
    for key, r in results.items():
        j = r["job"]
        rows[key] = {"date": today().isoformat(), "job_id": j.job_id, "company": j.company, "title": j.title, "score": j.score,
                     "posted": j.posted.isoformat() if j.posted else "", "link": j.link, "cv": r["cv"],
                     "status": r["status"], "reason": r["reason"], "unanswered": [u["label"] for u in r["unanswered"]]}
    MATCHES.write_text(json.dumps(rows, indent=1))


def run(cfg: dict, days: int | None, dry_run: bool, wanted: list[str] | None = None) -> dict:
    settings_ats, companies = load_companies()
    settings = llm_settings(cfg)
    threshold = cfg["run"]["match_threshold"]
    letters = bool((cfg.get("documents") or {}).get("write_cover_letters", True))
    days = days or settings_ats.get("first_run_days", 7)
    since = today() - dt.timedelta(days=days)
    calls_before = dict(llm.CALLS)
    out = run_dir() / "ats"
    out.mkdir(parents=True, exist_ok=True)
    s = _session()

    print(f"\nCompany job boards (Greenhouse, Lever, Ashby), last {days} days...")
    stats, jobs, failed = {}, [], []
    if not companies:
        print("  config/ats_companies.yaml has no companies. Add companies with their Greenhouse, Lever or Ashby slug.")
    boards = [c for c in companies if FEEDS.get(str(c.get("ats") or "").lower()) and c.get("slug")
              and (not wanted or any(w.lower() in c["name"].lower() for w in wanted))]      # --companies
    for n, c in enumerate(boards, 1):
        if progress.stop_requested():
            break
        feed = FEEDS[str(c["ats"]).lower()]
        progress.say(f"Checking {c['name']}, {n} of {len(boards)} companies")
        try:
            listed = feed(s, c["name"], c["slug"])
        except Exception as e:
            failed.append((c["name"], f"{e.__class__.__name__}: {e}"[:150]))
            print(f"  {c['name']}: failed ({e.__class__.__name__})")
            continue
        recent = [j for j in listed if not j.posted or j.posted >= since]
        stats[c["name"]] = {"ats": c["ats"], "uk": len(listed), "recent": len(recent), "target": 0, "claude": 0,
                            "matches": 0, "auto": 0, "manual": 0}
        jobs += recent
    print(f"  {sum(v['uk'] for v in stats.values())} UK jobs on {len(stats)} boards, {len(jobs)} posted in the last {days} days")

    job_boards = cfg["run"].get("job_boards") or []
    jobs, dropped = rules.dedupe(jobs, fuzzy=cfg["run"].get("fuzzy_title_duplicates", True), job_boards=job_boards)
    for job in jobs:
        if reason := rules.title_excluded(job, cfg):
            job.status, job.reason = "skipped", reason
            continue
        if not scoring.is_target_title(job.title, job.description):
            job.status, job.reason = "skipped", OFF_TARGET
            continue
        stats[job.company]["target"] += 1
        if reason := rules.skip_reason(job, cfg) or rules.below_min_salary(job, cfg):
            job.status, job.reason = "skipped", reason
            continue
        scoring.score(job)
        job.rules_score, job.scored_by = job.score, "rules"
        job.base_cv = scoring.base_cv_for(job.title)

    candidates = sorted([j for j in jobs if j.status == "new" and j.score >= settings["score_from"]], key=lambda j: -j.score)
    if settings["max_scores"] and len(candidates) > settings["max_scores"]:
        for job in candidates[settings["max_scores"]:]:
            job.score_notes.insert(0, "not sent to Claude: max_llm_scores_per_run reached; rule score kept")
        candidates = candidates[:settings["max_scores"]]
    print(f"  {sum(v['target'] for v in stats.values())} target titles; {len(candidates)} scored "
          f"{settings['score_from']}+ on the rules, scoring with Claude ({settings['score_model']})...")
    claude_score.score_all(candidates, cfg)
    for job in candidates:
        stats[job.company]["claude"] += 1
    for job in jobs:
        scoring.check_line_management(job)
        if job.status == "new":
            limit = scoring.threshold_for(job.title, cfg)
            job.status, job.reason = ("eligible", "") if job.score >= limit else \
                ("skipped", f"match score {job.score} below threshold {limit}")

    to_review = [review.entry(j, today().isoformat()) for j in jobs
                 if j.reason.startswith("missing essential") and j.missing_essentials]
    added_review = review.add(to_review, cfg) if to_review else 0

    # Tailored CV and form check for each match
    eligible = sorted([j for j in jobs if j.status == "eligible"], key=lambda j: -j.score)
    answers = load_answers(strict=False)
    results = {}
    todo = eligible[:settings["max_tailor"]] if settings["max_tailor"] else eligible
    if todo:
        print(f"  {len(todo)} matches: tailoring CVs and reading the application forms...")
    for n, job in enumerate(todo, 1):
        if progress.stop_requested():
            break
        progress.say(f"Writing CVs and reading application forms, {n} of {len(todo)}")
        res = results[job.key] = {"job": job, "cv": "", "unanswered": [], "answered": [], "status": "", "reason": ""}
        try:
            qs = QUESTIONS[job._ats](s, job)
        except Exception as e:
            qs, res["form_error"] = [], f"could not read the form: {e.__class__.__name__}: {e}"[:200]
        for q in qs:
            if not q["required"]:
                continue
            value, note = answer_for(q, answers, letters)
            (res["answered"] if value else res["unanswered"]).append({"label": q["label"], "answer": value, "note": note})
        for tries in range(2):
            try:
                t = tailor.tailor(job, cfg, out, keep="pdf")
                break
            except Exception as e:      # Word did not produce the PDF: restart Word and retry once (Claude's draft is cached)
                t = {"ok": False, "errors": [f"{e.__class__.__name__}: {e}"]}
                if "did not produce" in str(e) and tries == 0:
                    tailor._restart_word()
                    continue
                break
        res["cv"] = str(Path(t["cv"]).with_suffix(".pdf")) if t.get("ok") else ""
        stats[job.company]["matches"] += 1
        if not t.get("ok"):
            res["status"], res["reason"] = "skipped", "CV failed the CV rules checks: " + "; ".join(t["errors"][:2])
        elif res.get("form_error"):
            res["status"], res["reason"] = "deferred", f"manual apply: {res['form_error']}"
        elif res["unanswered"]:
            res["status"] = "deferred"
            res["reason"] = "manual apply: unanswered questions: " + "; ".join(u["label"][:80] for u in res["unanswered"])
        elif dry_run:
            res["status"], res["reason"] = "dry_run", "dry run: every required question covered by answers.yaml; nothing submitted"
        else:
            res["status"], res["reason"] = "deferred", "ready to auto-apply; ATS applying is not switched on yet, nothing submitted"
        key = "auto" if res["status"] in ("dry_run",) or res["reason"].startswith("ready") else "manual"
        if res["unanswered"]:
            unanswered.add(job, res["unanswered"])
        if t.get("ok") and res["reason"].startswith("manual apply"):
            byhand.add(job, res["cv"], res["reason"])
        if t.get("ok"):
            stats[job.company][key] += 1
        print(f"  {'auto  ' if key == 'auto' else 'manual'} {job.title} | {job.company} ({job.score})"
              + (f": {len(res['unanswered'])} unanswered" if res["unanswered"] else ""))
    for job in eligible[len(todo):]:
        job.reason = "above threshold, not tailored this run (max_tailor_per_run)"

    _write_report(out, jobs, dropped, results, stats, failed, dry_run, days)

    mode = "dry_run" if dry_run else "live"
    entries = []
    for job in jobs + dropped:
        if job.reason == "already seen":
            continue
        r = results.get(job.key)
        status, reason = (r["status"], r["reason"]) if r else \
            (("deferred", job.reason or "above threshold, not tailored this run") if job.status == "eligible" else (job.status, job.reason))
        if status == "deferred" and progress.STOP.exists():
            reason = progress.STOPPED
        entries.append({"job": job, "status": status, "reason": reason, "mode": mode})
    ledger.append(entries)
    save_matches(results)

    calls = {k: llm.CALLS[k] - calls_before.get(k, 0) for k in llm.CALLS}
    unanswered = Counter(u["label"].strip() for r in results.values() for u in r["unanswered"])
    print(f"\nATS report: {out / 'report.md'}")
    print(f"Missing essentials review (ATS): {len(to_review)} this run, {added_review} new on the list")
    print(f"Claude calls (ATS): {calls['made']} made, {calls['cached']} answered from the cache")
    return {"stats": stats, "failed": failed, "results": results, "unanswered": unanswered, "calls": calls,
            "review": len(to_review)}


def _write_report(out: Path, jobs, dropped, results, stats, failed, dry_run, days) -> None:
    md = review._md
    cv_link = lambda p: f"[CV]({Path(p).name})" if p else ""
    auto = [r for r in results.values() if r["status"] == "dry_run" or r["reason"].startswith("ready")]
    manual = [r for r in results.values() if r not in auto]
    lines = [f"# Company job boards {today().isoformat()}{' (dry run)' if dry_run else ''}", "",
             f"Greenhouse, Lever and Ashby boards in ats_companies.yaml, UK jobs posted in the last {days} days. "
             "Nothing was submitted.", "",
             "| Company | ATS | UK jobs | Last N days | Target titles | Scored by Claude | Matches | Auto-apply ready | Manual |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c, v in sorted(stats.items(), key=lambda x: (-x[1]["matches"], x[0])):
        lines.append(f"| {c} | {v['ats']} | {v['uk']} | {v['recent']} | {v['target']} | {v['claude']} | {v['matches']} | {v['auto']} | {v['manual']} |")
    lines += [f"| {c} | failed: {md(e)} | | | | | | | |" for c, e in failed]

    lines += ["", f"## Ready to auto-apply ({len(auto)})", "", "Every required question is answered from answers.yaml.", "",
              "| Company | Job title | Score | Link | CV | Answers used |", "|---|---|---|---|---|---|"]
    for r in sorted(auto, key=lambda r: -r["job"].score):
        j = r["job"]
        used = "<br>".join(f"{md(a['label'][:60])}: {md(a['answer'])}" for a in r["answered"])
        lines.append(f"| {md(j.company)} | {md(j.title)} | {j.score} | {j.link} | {cv_link(r['cv'])} | {used} |")

    lines += ["", f"## Apply by hand ({len(manual)})", "",
              "| Company | Job title | Score | Link | CV | Why |", "|---|---|---|---|---|---|"]
    for r in sorted(manual, key=lambda r: -r["job"].score):
        j = r["job"]
        why = r.get("form_error") or "<br>".join(f"{md(u['label'][:90])} ({md(u['note'])})" for u in r["unanswered"]) or md(r["reason"])
        lines.append(f"| {md(j.company)} | {md(j.title)} | {j.score} | {j.link} | {cv_link(r['cv'])} | {why} |")

    counts = Counter((u["label"].strip(), u["note"]) for r in results.values() for u in r["unanswered"])
    if counts:
        lines += ["", "## Unanswered questions (add to answers.yaml to unlock more jobs)", "",
                  "| Question | Jobs | Why |", "|---|---|---|"]
        lines += [f"| {md(q[:150])} | {n} | {md(note)} |" for (q, note), n in counts.most_common()]

    skipped = sorted([j for j in jobs if j.status == "skipped" and j.reason != OFF_TARGET and not j.reason.startswith("title contains")],
                     key=lambda j: -(j.score or -1))
    lines += ["", "## Target titles not matched", "", "| Company | Job title | Score | Reason | Link |", "|---|---|---|---|---|"]
    lines += [f"| {md(j.company)} | {md(j.title)} | {md(j.score)} | {md(j.reason)} | {j.link} |" for j in skipped]
    (out / "report.md").write_text("\n".join(lines) + "\n")
