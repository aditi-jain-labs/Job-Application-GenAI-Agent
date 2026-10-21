"""Job search APIs: Reed and Adzuna."""
import datetime as dt
import re
import time

import requests

from .models import Job
from .settings import env

REED_SEARCH = "https://www.reed.co.uk/api/1.0/search"
ADZUNA_SEARCH = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"
NATIONWIDE = {"", "united kingdom", "uk", "great britain", "england"}
AGENCY_WORDS = re.compile(r"\b(recruit\w*|resourcing|staffing|talent|search|headhunt\w*|consultancy group)\b", re.I)


def _date(value: str | None, fmt: str = "%d/%m/%Y") -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value[:10] if "-" in value else value, fmt).date()
    except ValueError:
        return None


def _reed_pages(params: dict, session: requests.Session):
    skip = 0
    while True:
        r = session.get(REED_SEARCH, params={**params, "resultsToTake": 100, "resultsToSkip": skip}, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
        yield from results
        if len(results) < 100:
            return
        skip += 100
        time.sleep(0.3)


def search_titles(cfg: dict) -> list[str]:
    """search.titles as search phrases: "Solutions Engineer (AI or Data)" becomes "Solutions Engineer" (the title
    filter then keeps only the AI and data ones). Repeats removed."""
    out = []
    for title in cfg["search"]["titles"]:
        phrase = re.sub(r"\s*\(.*?\)\s*", " ", title).strip()
        if phrase.lower() not in (o.lower() for o in out):
            out.append(phrase)
    return out


def fetch_reed(cfg: dict, days: int) -> list[Job]:
    search = cfg["search"]
    since = dt.date.today() - dt.timedelta(days=days)
    location = (search.get("location") or "").strip()
    session = requests.Session()
    session.auth = (env("REED_API_KEY"), "")

    base = {"minimumSalary": search.get("min_salary") or None}
    places = [{"locationName": location, "distanceFromLocation": search.get("distance_miles", 10)}]
    # A nationwide search already includes remote jobs. A city search needs a second, remote only pass.
    remote_pass = search.get("include_remote") and location.lower() not in NATIONWIDE
    if remote_pass:
        places.append({})

    jobs: dict[str, Job] = {}
    for title in search_titles(cfg):
        for place in places:
            for flag, is_agency in (("postedByRecruitmentAgency", True), ("postedByDirectEmployer", False)):
                params = {**base, **place, "keywords": f'"{title}"', flag: "true"}
                for r in _reed_pages(params, session):
                    posted = _date(r.get("date"))
                    if posted and posted < since:
                        continue
                    if not place and "remote" not in f"{r.get('locationName')} {r.get('jobTitle')} {r.get('jobDescription')}".lower():
                        continue
                    jid = str(r["jobId"])
                    if jid in jobs:
                        continue
                    jobs[jid] = Job(
                        source="reed",
                        job_id=jid,
                        title=r.get("jobTitle", "").strip(),
                        company=(r.get("employerName") or "").strip(),
                        location=r.get("locationName") or "",
                        link=r.get("jobUrl") or f"https://www.reed.co.uk/jobs/{jid}",
                        posted=posted,
                        expires=_date(r.get("expirationDate")),
                        salary_min=r.get("minimumSalary"),
                        salary_max=r.get("maximumSalary"),
                        is_agency=is_agency,
                        snippet=r.get("jobDescription") or "",
                    )
    return list(jobs.values())


def fetch_adzuna(cfg: dict, days: int) -> list[Job]:
    search = cfg["search"]
    app_id, app_key = env("ADZUNA_APP_ID"), env("ADZUNA_APP_KEY")
    location = (search.get("location") or "").strip()
    jobs: dict[str, Job] = {}
    for title in search_titles(cfg):
        page = 1
        while True:
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "what_phrase": title,
                "max_days_old": days,
                "results_per_page": 50,
                "content-type": "application/json",
            }
            if location.lower() not in NATIONWIDE:
                params["where"] = location
                params["distance"] = round(search.get("distance_miles", 10) * 1.609)
            if search.get("min_salary"):
                params["salary_min"] = search["min_salary"]
            r = requests.get(ADZUNA_SEARCH.format(page=page), params=params, timeout=30)
            r.raise_for_status()
            results = r.json().get("results", [])
            for a in results:
                jid = str(a["id"])
                company = (a.get("company") or {}).get("display_name", "").strip()
                jobs.setdefault(jid, Job(
                    source="adzuna",
                    job_id=jid,
                    title=a.get("title", "").strip(),
                    company=company,
                    location=(a.get("location") or {}).get("display_name", ""),
                    link=a.get("redirect_url", ""),
                    posted=_date(a.get("created"), "%Y-%m-%d"),
                    salary_min=a.get("salary_min"),
                    salary_max=a.get("salary_max"),
                    is_agency=bool(AGENCY_WORDS.search(company)),
                    snippet=a.get("description", ""),
                    contract_type=a.get("contract_type", "") or "",
                ))
            if len(results) < 50 or page >= 20:
                break
            page += 1
            time.sleep(0.5)
    return list(jobs.values())


def fetch_all(cfg: dict, days: int) -> list[Job]:
    jobs: list[Job] = []
    if cfg["sources"].get("reed"):
        jobs += fetch_reed(cfg, days)
    if cfg["sources"].get("adzuna"):
        jobs += fetch_adzuna(cfg, days)
    jobs.sort(key=lambda j: j.posted or dt.date.min, reverse=True)
    return jobs
