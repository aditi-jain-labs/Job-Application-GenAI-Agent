"""Referral monitor: UK jobs at the companies in referrals.yaml, checked on their own career sites.

It NEVER applies. Matching jobs get a tailored CV and a row in output/referral_jobs_available.md,
so you can ask your contact for a referral first. Same rules as the Reed run: target titles and
exclude_title_words (checked before any description is fetched), permanent only, min salary,
sponsorship, clearance, missing essentials (also added to the review list), rule score, then Claude
for jobs at llm.rule_score_min_for_llm or above, and match_threshold.

The first time a company is checked, only jobs posted in the last run.first_run_days days count.
After that, every job not seen before. Seen jobs live in cache/referrals_seen.json.
A site that shows a bot check, asks for a login or fails is skipped and listed as "set up a job alert".
"""
import datetime as dt
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from bs4 import BeautifulSoup

from . import byhand, claude_score, details, llm, progress, review, rules, scoring, tailor
from .models import Job
from .settings import REFERRALS_PATH, CACHE_DIR, ROOT, llm_settings, run_dir, today

SEEN = CACHE_DIR / "referrals_seen.json"
MATCHES = CACHE_DIR / "referral_jobs.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
BOT_CHECK = re.compile(r"captcha|are you (a )?human|are you a robot|challenge validation|just a moment|access denied|verify you are", re.I)
WORKDAY_UK = "29247e57dbaf46fb855b224e03170bc7"     # Workday's id for United Kingdom, the same on every tenant
OFF_TARGET = "title is not a target role"


class Blocked(Exception):
    """Bot check, login or a page we can't read reliably: set up a job alert instead."""


# ---------- HTTP ----------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})
    return s


def _check(r: requests.Response) -> requests.Response:
    if r.status_code in (401, 403, 429):
        raise Blocked(f"HTTP {r.status_code}")
    if "login" in urlparse(r.url).path.lower():
        raise Blocked("asks for a login")
    if "html" in r.headers.get("content-type", ""):
        head = BeautifulSoup(r.text[:5000], "html.parser").title
        if head and BOT_CHECK.search(head.get_text()):
            raise Blocked(f"bot check (\"{head.get_text().strip()}\")")
    r.raise_for_status()
    time.sleep(0.3)
    return r


def _json(r: requests.Response):
    try:
        return _check(r).json()
    except ValueError:
        raise Blocked("returned a page instead of job data")


def _text(html: str) -> str:
    return details.html_to_text(html or "")


def _date(value) -> dt.date | None:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%a %b %d %H:%M:%S %Z %Y"):
        try:
            return dt.datetime.strptime(value[:10] if fmt == "%Y-%m-%d" else value, fmt).date()
        except ValueError:
            continue
    return None


def _job(slug: str, company: str, job_id, title: str, location: str, link: str, posted=None, description: str = "",
         contract_type: str = "") -> Job:
    return Job(source=f"ref_{slug}", job_id=f"{slug}-{job_id}", title=" ".join(title.split()), company=company,
               location=location or "", link=link, posted=posted, description=description, contract_type=contract_type)


# ---------- one reader per job system ----------
# Each lists the company's UK jobs (cheap list calls only). Readers whose list has no description
# set job._detail, a function that fetches it; it is only called for target titles.

def workday(s, company, slug, url):
    u = urlparse(url)
    tenant, site = u.netloc.split(".")[0], u.path.strip("/").split("/")[-1]
    api = f"https://{u.netloc}/wday/cxs/{tenant}/{site}"
    first = _json(s.post(f"{api}/jobs", json={"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}}))
    # Filter to the UK where the site offers a country filter; otherwise the detail page says the country
    has_country = any(v.get("facetParameter") == "locationCountry" and any(x["id"] == WORKDAY_UK for x in v["values"])
                      for f in first.get("facets", []) for v in f.get("values", []) if "values" in v)
    facets = {"locationCountry": [WORKDAY_UK]} if has_country else {}
    jobs, offset, total = [], 0, None
    while total is None or offset < total:
        page = _json(s.post(f"{api}/jobs", json={"limit": 20, "offset": offset, "searchText": "", "appliedFacets": facets}))
        total = page["total"] if total is None else total
        posts = page.get("jobPostings") or []
        if not posts:
            break
        for p in posts:
            if not p.get("title") or not p.get("externalPath"):
                continue
            ago = re.search(r"(\d+)\+? Days? Ago|(Today)|(Yesterday)", p.get("postedOn", ""))
            days = 0 if ago and ago.group(2) else 1 if ago and ago.group(3) else int(ago.group(1)) if ago else None
            job = _job(slug, company, (p.get("bulletFields") or [p["externalPath"]])[0], p["title"], p.get("locationsText", ""),
                       f"https://{u.netloc}/{site}{p['externalPath']}",
                       today() - dt.timedelta(days=days) if days is not None else None)
            job._uk = has_country or None

            def detail(job=job, path=p["externalPath"]):
                info = _json(s.get(f"{api}{path}"))["jobPostingInfo"]
                job.description = _text(info.get("jobDescription"))
                job.location = info.get("location") or job.location
                job.posted = _date(info.get("startDate")) or job.posted
                if job._uk is None:
                    country = (info.get("country") or {}).get("descriptor", "")
                    extra = " ".join(info.get("additionalLocations") or [])
                    job._uk = "United Kingdom" in country or bool(re.search(r"\b(GBR|United Kingdom|UK)\b", extra))
            job._detail = detail
            jobs.append(job)
        offset += 20
    return jobs


def oracle(s, company, slug, url):
    u = urlparse(url)
    site = re.search(r"/sites/([^/]+)", u.path).group(1)
    api = f"https://{u.netloc}/hcmRestApi/resources/latest"
    find = lambda extra: _json(s.get(f"{api}/recruitingCEJobRequisitions", params={
        "onlyData": "true", "expand": "requisitionList", "finder": f"findReqs;siteNumber={site},{extra}"}))["items"][0]
    facets = find("facetsList=LOCATIONS,limit=1")
    uk = next(f["Id"] for f in facets.get("locationsFacet", []) if f.get("Name") == "United Kingdom")
    jobs, offset = [], 0
    while True:
        page = find(f"locationId={uk},limit=200,offset={offset},sortBy=POSTING_DATES_DESC")
        reqs = page.get("requisitionList") or []
        for r in reqs:
            job = _job(slug, company, r["Id"], r["Title"], r.get("PrimaryLocation", ""),
                       f"https://{u.netloc}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}", _date(r.get("PostedDate")))
            job._uk = True

            def detail(job=job, rid=r["Id"]):
                d = _json(s.get(f"{api}/recruitingCEJobRequisitionDetails", params={
                    "expand": "all", "onlyData": "true", "finder": f'ById;Id="{rid}",siteNumber={site}'}))["items"][0]
                job.description = _text("\n".join(d.get(k) or "" for k in (
                    "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))
            job._detail = detail
            jobs.append(job)
        offset += len(reqs)
        if not reqs or offset >= page.get("TotalJobsCount", 0):
            return jobs


def amazon(s, company, slug, url):
    jobs, offset = [], 0
    while True:
        page = _json(s.get("https://www.amazon.jobs/en/search.json",
                           params={"country": "GBR", "result_limit": 100, "offset": offset, "sort": "recent"}))
        for r in page["jobs"]:
            text = "\n".join([r.get("description") or "", "Basic qualifications", r.get("basic_qualifications") or "",
                              "Preferred qualifications", r.get("preferred_qualifications") or ""])
            job = _job(slug, company, r["id_icims"], r["title"], r.get("normalized_location") or r.get("location", ""),
                       "https://www.amazon.jobs" + r["job_path"], _date(r.get("posted_date")), _text(text))
            job._uk = r.get("country_code") == "GBR"
            jobs.append(job)
        offset += len(page["jobs"])
        if not page["jobs"] or offset >= page["hits"]:
            return jobs


def capgemini(s, company, slug, url):
    jobs, n = [], 1
    while True:
        page = _json(s.get("https://cg-jobstream-api.azurewebsites.net/api/job-search",
                           params={"page": n, "size": 100, "country_code": "gb-en"}))
        for r in page["data"]:
            job = _job(slug, company, r["ref"], r["title"], r.get("location", ""), r.get("apply_job_url") or url,
                       _date(r.get("indexed_at")), _text(r.get("description")), r.get("contract_type") or "")
            job._uk = True
            jobs.append(job)
        if not page["data"] or n * 100 >= page["count"]:
            return jobs
        n += 1


def goldman(s, company, slug, url):
    query = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $searchQueryInput) "
             "{ totalCount items { roleId jobTitle createdDate descriptionHtml externalSource { sourceId } locations { city country } } } }")
    jobs, n = [], 0
    while True:
        inp = {"page": {"pageSize": 100, "pageNumber": n}, "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
               "filters": [], "experiences": ["PROFESSIONAL"], "searchTerm": ""}
        res = _json(s.post("https://api-higher.gs.com/gateway/api/v1/graphql",
                           json={"operationName": "GetRoles", "variables": {"searchQueryInput": inp}, "query": query}))
        found = res["data"]["roleSearch"]
        for r in found["items"]:
            places = r.get("locations") or []
            sid = (r.get("externalSource") or {}).get("sourceId") or r["roleId"]
            job = _job(slug, company, sid, r["jobTitle"], "; ".join(f"{p['city']}, {p['country']}" for p in places),
                       f"https://higher.gs.com/roles/{sid}", _date(r.get("createdDate")), _text(r.get("descriptionHtml")))
            job._uk = any(p.get("country") == "United Kingdom" for p in places)
            jobs.append(job)
        n += 1
        if not found["items"] or n * 100 >= found["totalCount"]:
            return jobs


def jibe(s, company, slug, url):
    """iCIMS sites with a Jibe job search (ZS)."""
    host = urlparse(url).netloc
    jobs, n = [], 1
    while True:
        # An en-GB Accept-Language makes the Jibe search return nothing, so ask for its default language
        page = _json(s.get(f"https://{host}/api/jobs", params={"page": n, "limit": 100}, headers={"Accept-Language": "en-US"}))
        for r in (j["data"] for j in page["jobs"]):
            text = "\n".join(r.get(k) or "" for k in ("description", "responsibilities", "qualifications"))
            job = _job(slug, company, r["req_id"], r["title"], r.get("full_location", ""),
                       (r.get("meta_data") or {}).get("canonical_url") or f"https://{host}/jobs/{r['req_id']}",
                       _date(r.get("posted_date")), _text(text))
            job._uk = r.get("country_code") == "GB"
            jobs.append(job)
        if not page["jobs"] or n * 100 >= page["totalCount"]:
            return jobs
        n += 1


def successfactors(s, company, slug, url):
    """SAP SuccessFactors career sites (EY): the search page is HTML, 25 jobs a page."""
    u = urlparse(url)
    base, prefix = f"{u.scheme}://{u.netloc}", "/" + u.path.strip("/").split("/")[0]
    jobs, start, total = [], 0, None
    while total is None or start < total:
        r = _check(s.get(f"{base}{prefix}/search/", params={"optionsFacetsDD_country": "GB", "sortColumn": "referencedate",
                                                            "sortDirection": "desc", "startrow": start}))
        soup = BeautifulSoup(r.text, "html.parser")
        if total is None:
            m = re.search(r"of\s*<b>(\d+)</b>", r.text)
            if not m:
                raise Blocked("job list not found on the search page")
            total = int(m.group(1))
        rows = soup.select("tr.data-row")
        if not rows:
            break
        for row in rows:
            a = row.select_one("a.jobTitle-link")
            jid = re.search(r"/(\d+)/?$", a["href"]).group(1)
            loc = row.select_one(".jobLocation")
            job = _job(slug, company, jid, a.get_text(), loc.get_text(" ", strip=True).replace("+", " +") if loc else "",
                       base + a["href"])
            job._uk = True

            def detail(job=job):
                page = BeautifulSoup(_check(s.get(job.link)).text, "html.parser")
                body = page.select_one("span.jobdescription, [itemprop=description]")
                if not body:
                    raise Blocked("job page has no description")
                job.description = _text(str(body))
                when = page.select_one("[itemprop=datePosted]")
                job.posted = _date(when.get("content")) if when else None
            job._detail = detail
            jobs.append(job)
        start += 25
    return jobs


def reader_for(entry: dict):
    """The reader for a referrals.yaml entry, or None when it needs a job alert."""
    ats, url = (entry.get("ats") or "").lower(), entry.get("careers_url") or ""
    host = urlparse(url).netloc
    if not url or "job alert" in ats:
        return None
    if "myworkdayjobs.com" in host:
        return workday
    if "oraclecloud.com" in host:
        return oracle
    if "amazon.jobs" in host:
        return amazon
    if "capgemini.com" in host:
        return capgemini
    if "higher.gs.com" in host:
        return goldman
    if "icims" in ats:
        return jibe
    if "successfactors" in ats:
        return successfactors
    return None


# ---------- storage ----------

def load_entries() -> list[dict]:
    return (yaml.safe_load(REFERRALS_PATH.read_text()) or {}).get("companies") or []


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write_matches(rows: list[dict], manual: list[dict], cfg_ref: dict) -> Path:
    path = ROOT / (cfg_ref.get("output_file") or "output/referral_jobs_available.md")
    lines = ["# Referral jobs available", "",
             "Matching jobs at companies where you can get a referral, newest first. Nothing here has been applied to: "
             "ask your contact for a referral, then apply on the company's site with the CV listed.", "",
             "| Date | Company | Job title | Location | Score | Link | CV |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        cv = f"[{Path(r['cv']).name}]({Path(r['cv']).relative_to(path.parent)})" if r.get("cv") else "(CV failed the checks)"
        lines.append(f"| {r['date']} | {review._md(r['company'])} | {review._md(r['title'])} | {review._md(r['location'])} | "
                     f"{r['score']} | {r['link']} | {cv} |")
    if manual:
        lines += ["", "## Set up a job alert", "", "These sites can't be checked automatically. Set up a job alert on each.", "",
                  "| Company | Why | Link |", "|---|---|---|"]
        lines += [f"| {m['company']} | {review._md(m['why'])} | {m['link']} |" for m in manual]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def add_to_csv(new_rows: list[dict]) -> int:
    """New matches also go to output/referral_jobs_available.csv (newest first), where Referral asked and Applied are ticked."""
    rows = byhand._read_csv(byhand.REFERRAL_CSV)
    fields = list(rows[0].keys()) if rows else byhand.REFERRAL_FIELDS
    have = {r.get("Link") for r in rows}
    add = []
    for r in new_rows:
        if r["link"] in have:
            continue
        note = "stretch: software engineering role" if scoring.is_software_role(r["title"]) else ""
        add.append({**{k: "" for k in fields}, "Date": r["date"], "Company": r["company"], "Job title": r["title"],
                    "Location": r.get("location", ""), "Score": r["score"], "Link": r["link"],
                    "CV": str(Path(r["cv"]).resolve().relative_to(ROOT)) if r.get("cv") else "", "Note": note})
    if add:
        byhand._write_csv(byhand.REFERRAL_CSV, add + rows, fields)
    return len(add)


def notify(n: int) -> None:
    msg = f"New referral jobs available: {n}"
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Job agent"'], check=False)


# ---------- the run ----------

def run(cfg: dict, wanted: list[str] | None = None) -> dict:
    cfg_ref = yaml.safe_load(REFERRALS_PATH.read_text()) or {}
    settings = llm_settings(cfg)
    threshold = cfg["run"]["match_threshold"]
    window = cfg["run"].get("first_run_days", 14)
    seen = _load(SEEN, {})
    calls_before = dict(llm.CALLS)
    out = run_dir() / "referrals"
    out.mkdir(parents=True, exist_ok=True)

    print("\nReferral monitor (never applies)...")
    s = _session()
    stats, manual, jobs = {}, [], []
    entries = [e for e in load_entries() if not wanted or any(w.lower() in e["name"].lower() for w in wanted)]   # --companies
    if not load_entries():
        print("  config/referrals.yaml has no companies yet. Add the companies where you can get a referral.")
    for n, entry in enumerate(entries, 1):
        if progress.stop_requested():
            break
        name, url = entry["name"], entry.get("careers_url") or ""
        progress.say(f"Checking {name}, {n} of {len(entries)} companies")
        reader = reader_for(entry)
        if reader is None:
            manual.append({"company": name, "why": entry.get("ats") or "no job system set", "link": url})
            continue
        slug = re.sub(r"[^a-z0-9]+", "", name.lower().split("(")[0])[:12]
        try:
            listed = reader(s, name, slug, url)
        except Blocked as e:
            manual.append({"company": name, "why": f"skipped: {e}", "link": url})
            print(f"  {name}: {e}; set up a job alert")
            continue
        except Exception as e:      # a site change should not stop the other companies
            manual.append({"company": name, "why": f"failed: {e.__class__.__name__}: {e}"[:200], "link": url})
            print(f"  {name}: failed ({e.__class__.__name__}: {e}); set up a job alert")
            continue

        first = name not in seen
        known = seen.setdefault(name, {})
        st = stats[name] = {"listed": len(listed), "new": 0, "target": 0, "passed_rules": 0, "claude": 0, "matches": 0, "first": first}
        since = today() - dt.timedelta(days=window)
        for job in listed:
            if job.job_id in known or job._uk is False:
                continue
            if first and job.posted and job.posted < since:
                known[job.job_id] = today().isoformat()      # too old for the first run; never looked at again
                continue
            st["new"] += 1
            job._company = name
            jobs.append(job)
        print(f"  {name}: {len(listed)} listed, {st['new']} new{' (first run: last ' + str(window) + ' days)' if first else ''}")

    # Cheap title checks first, so no description is fetched for a job we'd never use
    for n, job in enumerate(jobs, 1):
        known = seen[job._company]
        if progress.stop_requested():
            job.status, job.reason = "deferred", progress.STOPPED      # not marked seen: looked at next run
            continue
        progress.say(f"Reading new job listings, {n} of {len(jobs)}")
        if reason := rules.title_excluded(job, cfg):
            job.status, job.reason = "skipped", reason
        elif not scoring.needs_intro(job.title) and not scoring.is_target_title(job.title, job.description):
            job.status, job.reason = "skipped", OFF_TARGET
        if job.status == "skipped":
            known[job.job_id] = today().isoformat()
            continue
        stats[job._company]["target"] += 1
        try:
            if getattr(job, "_detail", None):
                job._detail()
        except Blocked as e:
            job.status, job.reason = "skipped", f"job page blocked: {e}"
            continue                    # not marked seen: tried again next run
        except requests.RequestException as e:
            job.status, job.reason = "skipped", f"page did not load: {e.__class__.__name__}"
            continue
        if scoring.needs_intro(job.title) and not scoring.is_target_title(job.title, job.description):
            job.status, job.reason = "skipped", OFF_TARGET     # Solutions or Customer Engineer with no AI or data in the intro
            known[job.job_id] = today().isoformat()
            continue
        if job._uk is False:
            job.status, job.reason = "skipped", "not in the UK"
            known[job.job_id] = today().isoformat()
            continue
        if stats[job._company]["first"] and job.posted and job.posted < today() - dt.timedelta(days=window):
            job.status, job.reason = "skipped", f"posted {job.posted.isoformat()}, before the first run window"
            known[job.job_id] = today().isoformat()
            continue
        reason = rules.skip_reason(job, cfg) or rules.below_min_salary(job, cfg)
        if not job.description.strip():
            reason = "no description found"
        if reason:
            job.status, job.reason = "skipped", reason
            known[job.job_id] = today().isoformat()
            continue
        scoring.score(job)
        job.rules_score, job.scored_by = job.score, "rules"
        job.base_cv = scoring.base_cv_for(job.title)
        stats[job._company]["passed_rules"] += 1

    candidates = sorted([j for j in jobs if j.status == "new" and j.score >= settings["score_from"]], key=lambda j: -j.score)
    held = []
    if settings["max_scores"] and len(candidates) > settings["max_scores"]:
        held, candidates = candidates[settings["max_scores"]:], candidates[:settings["max_scores"]]
    print(f"  {len(candidates)} jobs scored {settings['score_from']}+ on the rules; scoring with Claude ({settings['score_model']})...")
    claude_score.score_all(candidates, cfg)
    for job in candidates:
        stats[job._company]["claude"] += 1
    for job in jobs:
        scoring.check_line_management(job)
        if job.status != "new" or job in held:
            continue
        limit = scoring.threshold_for(job.title, cfg)
        job.status, job.reason = ("eligible", "") if job.score >= limit else \
            ("skipped", f"match score {job.score} below threshold {limit}")

    to_review = [review.entry(j, today().isoformat()) for j in jobs
                 if j.reason.startswith("missing essential") and j.missing_essentials]
    added_review = review.add(to_review, cfg) if to_review else 0

    # Tailored CV for each match. Never applied.
    eligible = sorted([j for j in jobs if j.status == "eligible"], key=lambda j: -j.score)
    tailor_now = eligible[:settings["max_tailor"]] if settings["max_tailor"] else eligible
    rows = _load(MATCHES, [])
    have = {r["job_id"] for r in rows}
    new_rows = []
    if tailor_now:
        print(f"  tailoring {len(tailor_now)} CVs...")
    for n, job in enumerate(tailor_now, 1):
        if progress.stop_requested():
            break
        progress.say(f"Writing CVs, {n} of {len(tailor_now)}")
        for tries in range(2):
            try:
                result = tailor.tailor(job, cfg, out, keep="docx")
                break
            except Exception as e:      # Word did not produce the PDF: restart Word and retry once (Claude's draft is cached)
                result = {"ok": False, "errors": [f"{e.__class__.__name__}: {e}"]}
                if "did not produce" in str(e) and tries == 0:
                    tailor._restart_word()
                    continue
                break
        print(f"  {'ok  ' if result['ok'] else 'FAIL'} {job.title} | {job._company} ({job.score})"
              + ("" if result["ok"] else f": {result['errors'][0][:120]}"))
        if not result["ok"] and any("did not produce" in e for e in result["errors"]):
            continue                    # not listed or marked seen: the next run tries the PDF again
        stats[job._company]["matches"] += 1
        seen[job._company][job.job_id] = today().isoformat()
        if job.job_id not in have:
            docx = Path(result["cv"]) if result.get("ok") else None      # Word file only; run.py --pdf JOB_ID makes the PDF
            new_rows.append({"date": today().isoformat(), "job_id": job.job_id, "company": job._company, "title": job.title,
                             "location": job.location, "score": job.score, "link": job.link,
                             "posted": job.posted.isoformat() if job.posted else "",
                             "cv": str(docx) if docx and docx.exists() else "", "errors": result.get("errors", [])})
    # Jobs held back by max_llm_scores_per_run or max_tailor_per_run are not marked seen, so the next run picks them up
    for job in jobs:
        if job.status == "skipped" and job.reason.startswith(("match score", "missing essential", "no sponsorship",
                                                                "DV clearance", "listing closed")):
            seen[job._company][job.job_id] = today().isoformat()

    rows = sorted(new_rows + rows, key=lambda r: r["date"], reverse=True)
    MATCHES.parent.mkdir(parents=True, exist_ok=True)
    MATCHES.write_text(json.dumps(rows, indent=1))
    SEEN.write_text(json.dumps(seen, indent=1))
    path = write_matches(rows, manual, cfg_ref)
    add_to_csv(new_rows)
    _write_screening(out, jobs, stats, manual)
    if new_rows:
        notify(len(new_rows))

    calls = {k: llm.CALLS[k] - calls_before.get(k, 0) for k in llm.CALLS}
    print(f"\nReferral jobs: {len(new_rows)} new matches -> {path}")
    print(f"Missing essentials review (referrals): {len(to_review)} this run, {added_review} new on the list")
    print(f"Claude calls (referrals): {calls['made']} made, {calls['cached']} answered from the cache")
    return {"stats": stats, "manual": manual, "new": new_rows, "calls": calls, "review": len(to_review)}


def _write_screening(out: Path, jobs: list[Job], stats: dict, manual: list[dict]) -> None:
    md = review._md
    lines = [f"# Referral screening {today().isoformat()}", "",
             "| Company | Listed (UK) | New | Target titles | Passed rules | Scored by Claude | Matches |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {c} | {s['listed']} | {s['new']} | {s['target']} | {s['passed_rules']} | {s['claude']} | {s['matches']} |"
              for c, s in stats.items()]
    lines += [f"| {m['company']} | {md(m['why'])} | | | | | |" for m in manual]
    lines += ["", "## Target titles", "", "| Company | Job title | Score | Status | Reason | Link |", "|---|---|---|---|---|---|"]
    for j in sorted((j for j in jobs if j.reason != OFF_TARGET and not j.reason.startswith("title contains")),
                    key=lambda j: -(j.score or -1)):
        lines.append(f"| {md(j._company)} | {md(j.title)} | {md(j.score)} | {j.status} | {md(j.reason)} | {j.link} |")
    (out / "screening.md").write_text("\n".join(lines) + "\n")
