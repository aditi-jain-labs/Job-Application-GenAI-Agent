"""Readers for PwC UK, Deloitte UK and KPMG UK careers sites (watch list).

Each reader lists the company's open UK jobs (title, link, grade where the list shows it) with the
cheapest public pages, and sets job._detail to fetch the full listing; that is only called for target titles.
- PwC UK: jobs.pwc.co.uk experienced sitemap, then each job page (its embedded job data). Never
  experiencedcareers.pwc.co.uk (blocks bots) or any /apply page.
- Deloitte UK: apply.deloitte.co.uk search pages (20 per page), then JobDetail pages.
- KPMG UK: kpmgcareers.co.uk search pages (?searchtext=&page=N), then vacancy pages. A blank vacancy
  page means the role is closed.
At most about 3 requests a second. A bot check, CAPTCHA or block raises Blocked: stop for that company.
"""
import datetime as dt
import html
import json
import re
import time

import requests
from bs4 import BeautifulSoup

from .apply import BOT_TEXT
from .details import BROWSER_UA, html_to_text
from .models import Job

GAP = 0.4           # seconds between requests
PWC_SITEMAP = "https://jobs.pwc.co.uk/experienced/uk/en/sitemap.xml"
DELOITTE_SEARCH = "https://apply.deloitte.co.uk/UKCareers/SearchJobs/?jobRecordsPerPage=20&jobOffset={offset}"
DELOITTE_JOB = "https://apply.deloitte.co.uk/UKCareers/JobDetail/job/{req}"
KPMG_SEARCH = "https://www.kpmgcareers.co.uk/search/vacancies/?searchtext=&page={page}"
GRADES = re.compile(r"\b(associate director|senior manager|assistant manager|senior consultant|senior associate|"
                    r"director|manager|consultant|associate|analyst|partner)\b", re.I)
STRETCH_GRADES = {"senior manager", "associate director", "director"}


class Blocked(Exception):
    pass


class Closed(Exception):
    pass


_last = {"t": 0.0}


def get(s: requests.Session, url: str) -> requests.Response:
    wait = GAP - (time.monotonic() - _last["t"])
    if wait > 0:
        time.sleep(wait)
    _last["t"] = time.monotonic()
    r = s.get(url, timeout=40)
    if r.status_code in (401, 403, 429) or BOT_TEXT.search(r.text[:30000]) or re.search(r"captcha", r.text[:30000], re.I):
        raise Blocked(f"bot check or block (HTTP {r.status_code}) on {url.split('?')[0]}")
    return r


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "en-GB,en;q=0.9"})
    return s


def grades_in(title: str) -> str:
    return "/".join(dict.fromkeys(g.title() for g in GRADES.findall(title)))


def stretch(grade: str) -> str:
    hit = [g for g in grade.split("/") if g.lower() in STRETCH_GRADES]
    return f"stretch: {hit[0]} grade" if hit else ""


def _job(company: str, prefix: str, job_id: str, title: str, location: str, link: str, posted=None, grade: str = "") -> Job:
    job = Job(source=prefix, job_id=f"{prefix}-{job_id}", title=" ".join(html.unescape(title).split()), company=company,
              location=location, link=link, posted=posted)
    job._grade = grade
    return job


# ---------- PwC UK ----------

def _slug_title(url: str) -> str:
    return re.sub(r"-+", " ", url.rstrip("/").rsplit("/", 1)[-1])


def pwc(s: requests.Session) -> list[Job]:
    xml = get(s, PWC_SITEMAP).text
    jobs = []
    for url in re.findall(r"<loc>([^<]+/job/([^/<]+)/[^<]*)</loc>", xml):
        link, seq = url
        job = _job("PwC UK", "pwc", seq, _slug_title(link), "", link)

        def detail(job=job, link=link):
            page = get(s, link).text
            m = re.search(r"phApp\.ddo = (\{.*?\});\s*phApp", page, re.S)
            if not m:
                raise Closed("no job data on the page")
            data = json.loads(m.group(1)).get("jobDetail", {}).get("data", {}).get("job") or {}
            if not data or data.get("postingStatus", "OPEN") != "OPEN":
                raise Closed("listing closed")
            job.title = " ".join(html.unescape(data.get("title") or job.title).split())
            job.location = data.get("cityStateCountry") or data.get("location") or ""
            job.description = html_to_text(data.get("jobDescription") or "")
            job.posted = dt.date.fromisoformat(data["postedDate"][:10]) if data.get("postedDate") else None
            job.contract_type = "permanent" if (data.get("workerType") or "Regular") == "Regular" else data["workerType"].lower()
            job.salary_max = float(data["maximumSalary"]) if str(data.get("maximumSalary") or "").isdigit() else None
            job._grade = data.get("grade") or grades_in(job.title)
            job._visa = data.get("visaAvailability") or ""
            job._uk = (data.get("country") or "United Kingdom") == "United Kingdom"
        job._detail = detail
        jobs.append(job)
    return jobs


# ---------- Deloitte UK ----------

def deloitte(s: requests.Session) -> list[Job]:
    jobs, offset, total = {}, 0, None
    while True:
        page = get(s, DELOITTE_SEARCH.format(offset=offset)).text
        if total is None:
            m = re.search(r"of\s+([\d,]+)\s+results", re.sub(r"<[^>]+>", " ", page))
            total = int(m.group(1).replace(",", "")) if m else 0
        soup = BeautifulSoup(page, "html.parser")
        found = 0
        for a in soup.select("a[href*='/UKCareers/JobDetail/']"):
            m = re.search(r"/JobDetail/([^/]+)/(\d+)", a["href"])
            title = a.get_text(" ", strip=True)
            if not m or not title or m.group(2) in jobs or title.lower() in ("apply now", "view job"):
                continue
            card = a.find_parent(["article", "li", "div"])
            text = card.get_text(" | ", strip=True) if card else ""
            posted = re.search(r"Posted:\s*(\d{1,2} \w{3} \d{4})", text)
            jobs[m.group(2)] = _job("Deloitte UK", "deloitte", m.group(2), title, "", a["href"].split("?")[0],
                                    dt.datetime.strptime(posted.group(1), "%d %b %Y").date() if posted else None, grades_in(title))
            found += 1
        offset += 20
        if not found or (total and offset >= total):
            break
    for job in jobs.values():
        job._detail = _deloitte_detail(s, job)
    return list(jobs.values())


def _deloitte_detail(s, job: Job):
    def detail():
        page = get(s, job.link).text
        soup = BeautifulSoup(page, "html.parser")
        text = soup.get_text("\n", strip=True)
        if "Job description" not in text:
            raise Closed("listing closed or gone")
        field = lambda name: (re.search(rf"^{name}\n(.+)$", text, re.M) or [None, ""])[1].strip()
        job.location = field("Location") or job.location
        jtype = field("Job Type")
        job.contract_type = "permanent" if jtype.lower().startswith("permanent") else jtype.lower()
        published = field("Date published")
        if published:
            try:
                job.posted = dt.datetime.strptime(published, "%d-%b-%Y").date()
            except ValueError:
                pass
        body = text.split("Job description", 1)[1]
        body = re.split(r"\n(?:Apply now|Share this job|Similar jobs)\n", body)[0]
        job.description = body.strip()
        job._uk = True
    return detail


def deloitte_req(s: requests.Session, req: str) -> Job:
    """A Deloitte job by its Req number (the JobDetail URL works with any slug)."""
    job = _job("Deloitte UK", "deloitte", req, "", "", DELOITTE_JOB.format(req=req))
    page = get(s, job.link).text
    soup = BeautifulSoup(page, "html.parser")
    h1 = soup.find("h1")
    job.title = " ".join((h1.get_text(" ", strip=True) if h1 else "").split())
    job._grade = grades_in(job.title)
    job._detail = _deloitte_detail(s, job)
    return job


# ---------- KPMG UK ----------

def kpmg(s: requests.Session) -> list[Job]:
    jobs, page_no, last = {}, 1, None
    while True:
        page = get(s, KPMG_SEARCH.format(page=page_no)).text
        if last is None:
            nums = [int(n) for n in re.findall(r"[?&](?:amp;)?page=(\d+)", page)]
            last = max(nums) if nums else 1
        soup = BeautifulSoup(page, "html.parser")
        cards = soup.select("div.vacancy-result")
        for card in cards:
            a = card.select_one("a[href*='/Vacancies/']")
            h3 = card.find("h3")
            if not a or not h3:
                continue
            vid = card.get("data-vacancy-id") or a["href"].rstrip("/").rsplit("/", 1)[-1]
            loc = card.select_one(".vacancy-location b")
            kind = card.select_one(".vacancy-type b")
            if vid in jobs or (kind and kind.get_text(strip=True).lower() not in ("experienced professional", "")):
                continue      # graduate, apprentice and student programmes are not experienced hires
            jobs[vid] = _job("KPMG UK", "kpmg", vid, h3.get_text(" ", strip=True), loc.get_text(strip=True) if loc else "",
                             "https://www.kpmgcareers.co.uk" + a["href"], None, grades_in(h3.get_text()))
        page_no += 1
        if not cards or page_no > last:
            break
    for job in jobs.values():
        job._detail = _kpmg_detail(s, job)
    return list(jobs.values())


def _kpmg_detail(s, job: Job):
    def detail():
        page = get(s, job.link).text
        soup = BeautifulSoup(page, "html.parser")
        desc = soup.select_one(".job-description") or soup.select_one(".vacancy-description-field")
        fields = {}
        for f in soup.select(".vacancy-details-fields"):
            t = f.get_text(" ", strip=True)
            if ":" in t:
                k, v = t.split(":", 1)
                fields[k.strip().lower()] = v.strip()
        if not desc or len(desc.get_text(strip=True)) < 50:
            raise Closed("blank vacancy page: the role is closed")
        job.description = html_to_text(str(desc))
        job.location = fields.get("location") or job.location
        ctype = fields.get("contract type", "")
        job.contract_type = "permanent" if ctype.lower().startswith("permanent") else ctype.lower()
        job._grade = fields.get("experience level") or job._grade
        job._uk = True
    return detail


READERS = {"PwC UK": pwc, "Deloitte UK": deloitte, "KPMG UK": kpmg}
