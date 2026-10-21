"""Fetch each job's full description. API search text is cut short."""
import json
import re
import time

import requests
from bs4 import BeautifulSoup

from .models import Job
from .settings import CACHE_DIR, env

REED_DETAILS = "https://www.reed.co.uk/api/1.0/jobs/{id}"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
NOT_FOUND = re.compile(r"page not found|404 error|this page (doesn't|does not) exist", re.I)


class PageMissing(Exception):
    pass


def html_to_text(html: str) -> str:
    """Plain text that keeps one line per paragraph or bullet, so requirement lists survive."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text()
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _cached(job: Job) -> dict | None:
    path = CACHE_DIR / job.source / f"{job.job_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _save(job: Job, data: dict) -> None:
    path = CACHE_DIR / job.source / f"{job.job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _from_page(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30, allow_redirects=True)
    if r.status_code == 404:
        raise PageMissing("page not found (404)")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text() if soup.title else ""
    if NOT_FOUND.search(title):
        raise PageMissing("page not found")
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return html_to_text(str(main))


def fetch_description(job: Job, session: requests.Session | None = None) -> None:
    """Fill job.description (and apply_url for Reed). Raises PageMissing if the job is gone."""
    data = _cached(job)
    if data is None:
        if job.source == "reed":
            session = session or requests.Session()
            session.auth = session.auth or (env("REED_API_KEY"), "")
            r = session.get(REED_DETAILS.format(id=job.job_id), timeout=30)
            if r.status_code == 404:
                raise PageMissing("page not found (Reed details 404)")
            r.raise_for_status()
            d = r.json()
            if not d or not d.get("jobDescription"):
                # Fall back to the public page
                data = {"description": _from_page(job.link)}
            else:
                data = {
                    "description": html_to_text(d["jobDescription"]),
                    "apply_url": d.get("externalUrl") or "",
                    "contract_type": d.get("contractType") or "",
                    "expires": d.get("expirationDate") or "",
                }
            time.sleep(0.2)
        else:
            data = {"description": _from_page(job.link)}
        _save(job, data)

    job.description = data["description"]
    if data.get("apply_url") and data["apply_url"] != job.link:
        job.apply_url = data["apply_url"]
    job.contract_type = data.get("contract_type") or job.contract_type
