"""Dedupe and skip rules (cv_rules_for_agent.md section 2, phrases in config.yaml)."""
import datetime as dt
import re

from .ledger import company_title, load_seen, norm
from .models import Job

# Common wordings the exact phrases in config.yaml miss, e.g. "no visa sponsorship".
SPONSOR_PATTERNS = [
    r"\b(unable|not able|cannot|can ?not|can'?t|won'?t|will not|do not|does not|don'?t|doesn'?t|are not|is not|not in a position)\s+(to\s+)?(offer|provide|support|sponsor|consider)\w*\s+(any\s+)?(visa\s+|work\s+(permit\s+)?|employment\s+)?sponsor",
    r"\bno\s+(visa\s+|work\s+(permit\s+)?)?sponsorship",
    r"\bsponsorship\s+(is\s+not|is\s+un|will\s+not|can\s*not|can'?t)\s*(be\s+)?(available|offered|provided|possible)",
    r"\b(cannot|can'?t|unable\s+to|not\s+able\s+to|will\s+not|won'?t)\s+(accept|consider)\s+(applications|applicants|candidates)\s+(\w+\s+){0,3}(requir|need)\w*\s+(visa\s+)?sponsorship",
    r"\b(must|should|will)\s+not\s+(now\s+or\s+in\s+the\s+future\s+)?(require|need)\s+(visa\s+|any\s+)?(form\s+of\s+)?sponsorship",
    r"\bwithout\s+(the\s+)?(need|requirement|requiring)\s+(for|of)?\s*(visa\s+|any\s+)?sponsorship",
    r"\bbritish\s+(citizens?|nationals?)\s+only\b",
    r"\b(must|need to)\s+be\s+a\s+british\s+(citizen|national)",
]
# Only DV (Developed Vetting) and enhanced DV are skipped. SC roles, SC eligibility and SC residency wording are allowed.
CLEARANCE_PATTERNS = [
    r"\b(e?dv)\s*(level\s+)?(security\s+)?(clearance|cleared|vetting|vetted|clearable)\b",
    r"\bdeveloped\s+vetting\b",
    r"\bactive\s+e?dv\b",
    r"\b(e?dv)\s+eligib\w*",
    r"\beligib\w*\s+(for|to\s+(obtain|gain|achieve))\s+e?dv\b",
]
# Signs a role is not permanent (config search.permanent_only)
NOT_PERMANENT_TITLE = re.compile(r"\b(contract|contractor|ftc|fixed[\s-]+term|temp|temporary|interim|freelance|day\s+rate)\b", re.I)
NOT_PERMANENT_TEXT = [
    r"(?<!/)\bday\s+rate\b", r"£\s*\d[\d,.]*\s*k?\s*(per|a|/)\s*day\b", r"\bper\s+day\b", r"\bumbrella\b",
    r"\b(inside|outside)\s+(of\s+)?ir35\b", r"\bfixed[\s-]+term\b", r"\bftc\b",
    r"\bcontract\s+(role|position|opportunity|assignment|basis)\b", r"\b\d+\s*(-|to)?\s*\d*\s*months?\s+(initial\s+)?contract\b",
    r"\binitial\s+\d+\s*(-|to)?\s*\d*\s*months?\b", r"\bfreelance\b",
]
CLOSED_PATTERNS = [
    r"\b(this\s+)?(job|vacancy|role|position|listing)\s+(has\s+)?(now\s+)?(closed|expired)\b",
    r"\bno\s+longer\s+accepting\s+applications\b",
    r"\bapplications\s+(are\s+)?(now\s+)?closed\b",
]


def _clean(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text)


def _find(text: str, phrases: list[str], patterns: list[str]) -> str | None:
    for p in phrases:
        if _clean(p) in text:
            return p
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0).strip()
    return None


# Words that mark where or how a job is done, not what it is. Titles from the same company that
# differ only by these count as one job. Tech tags such as (AWS) or (Azure) are not in here.
TITLE_NOISE = {
    "uk", "gb", "united", "kingdom", "england", "scotland", "wales", "northern", "ireland", "emea", "europe",
    "remote", "hybrid", "onsite", "site", "office", "fully", "flexible", "home", "based", "working", "wfh",
    "big", "4", "four", "perm", "permanent", "contract", "contractor", "ftc", "fixed", "term", "temp", "temporary",
    "full", "part", "time", "month", "months", "12m", "6m", "urgent", "immediate", "start", "new", "role", "job",
    "up", "to", "k", "bonus", "benefits", "salary", "competitive", "per", "annum", "day", "rate", "ir35", "inside",
    "outside", "days", "week", "x", "or", "and", "the", "in", "of", "a",
    "london", "manchester", "birmingham", "leeds", "bristol", "bath", "cardiff", "edinburgh", "glasgow", "belfast",
    "liverpool", "newcastle", "tyne", "upon", "sheffield", "nottingham", "leicester", "reading", "oxford",
    "cambridge", "milton", "keynes", "southampton", "portsmouth", "brighton", "exeter", "plymouth", "swindon",
    "cheltenham", "gloucester", "coventry", "york", "hull", "derby", "norwich", "ipswich", "chester", "warrington",
    "preston", "aberdeen", "dundee", "swansea", "sunderland", "durham", "guildford", "slough", "watford", "luton",
    "basingstoke", "sunbury", "croydon", "canary", "wharf", "city", "central", "north", "south", "east", "west",
    "midlands", "yorkshire", "surrey", "kent", "essex", "berkshire", "hampshire", "wiltshire", "somerset", "devon",
    "lancashire", "cheshire",
}


def fuzzy_title(title: str, location: str = "") -> str:
    """Title with location, work pattern, contract and salary tags removed."""
    noise = TITLE_NOISE | set(norm(location).split())
    words = [w for w in norm(re.sub(r"£\s*[\d,.]+\s*k?", " ", title)).split()
             if w not in noise and not re.fullmatch(r"\d+k?", w)]
    return " ".join(words) or norm(title)


def employer_in_title(title: str, company: str) -> str:
    """'Data Engineer - NatWest Group' -> 'NatWest Group'; '' when the title names no employer."""
    parts = [p.strip() for p in re.split(r"\s+[-–|]\s+", title)]
    if len(parts) > 1 and company.lower() not in parts[-1].lower():
        last = parts[-1]
        if not re.search(r"\b(london|remote|hybrid|uk|manchester|£|k\b|contract|perm)", last, re.I) \
                and not all(w in TITLE_NOISE for w in norm(last).split()):
            return last
    return ""


def dedupe(jobs: list[Job], fuzzy: bool = True, job_boards: list[str] = ()) -> tuple[list[Job], list[Job]]:
    """Drop jobs already in applied.csv or seen_jobs.csv, and repeats with the same company and title.

    With fuzzy on, titles that differ only by a location or tag ("Big 4", "London", "Remote")
    count as the same. Jobs are sorted newest first, so the newest listing is kept.
    Job boards repost many employers under one name. When the title names the employer
    ("Data Engineer - NatWest Group") fuzzy matching is safe; otherwise their listings only match on the
    exact title and location, since "Data Engineer" and "Data Engineer - Belfast" may be two employers.
    Returns (kept, dropped). Dropped jobs carry a reason but are not written back to applied.csv.
    """
    boards = [b.lower() for b in job_boards]

    def pair(company: str, title: str, location: str = "") -> str:
        if any(b in company.lower() for b in boards) and not employer_in_title(title, company):
            return company_title(company, f"{title} {location}")
        return company_title(company, fuzzy_title(title, location) if fuzzy else title)

    keys, links, seen_rows = load_seen()
    applied_pairs = {pair(c, t) for c, t in seen_rows}
    seen: dict[str, Job] = {}
    kept, dropped = [], []
    for j in jobs:
        p = pair(j.company, j.title, j.location)
        if j.key in keys or j.link in links or p in applied_pairs:
            j.status, j.reason = "duplicate", "already seen"
            dropped.append(j)
        elif p in seen:
            first = seen[p]
            same = norm(first.title) == norm(j.title)
            j.status = "duplicate"
            j.reason = ("same company and title as another listing" if same
                        else f"same company and title, apart from location or tags, as \"{first.title}\"")
            dropped.append(j)
        else:
            seen[p] = j
            kept.append(j)
    return kept, dropped


def title_excluded(job: Job, cfg: dict) -> str | None:
    words = cfg["search"].get("exclude_title_words") or []
    title = norm(job.title)
    for w in words:
        if re.search(rf"\b{re.escape(norm(w))}\b", title):
            return f"title contains '{w}'"
    return None


def not_permanent(job: Job, text: str) -> str | None:
    if job.contract_type and job.contract_type.lower() not in ("permanent", ""):
        return f"not permanent: Reed lists it as {job.contract_type.lower()}"
    if m := NOT_PERMANENT_TITLE.search(job.title):
        return f"not permanent: title says \"{m.group(0)}\""
    if hit := _find(text, [], NOT_PERMANENT_TEXT):
        return f"not permanent: \"{hit}\""
    return None


def skip_reason(job: Job, cfg: dict) -> str | None:
    """First matching skip rule on the full description, or None."""
    if r := title_excluded(job, cfg):
        return r
    if job.expires and job.expires < dt.date.today():
        return f"listing expired on {job.expires.isoformat()}"

    text = _clean(f"{job.title}\n{job.description}")
    if cfg["search"].get("permanent_only") and (reason := not_permanent(job, text)):
        return reason
    phrases = cfg.get("skip_phrases", {})
    if hit := _find(text, phrases.get("sponsorship", []), SPONSOR_PATTERNS):
        return f"no sponsorship: \"{hit}\""
    if hit := _find(text, phrases.get("clearance", []), CLEARANCE_PATTERNS):
        return f"DV clearance: \"{hit}\""
    if hit := _find(text, [], CLOSED_PATTERNS):
        return f"listing closed: \"{hit}\""
    return None


SALARY = re.compile(r"£\s?(\d{2,3})(?:,(\d{3})|(k))\b", re.I)


def below_min_salary(job: Job, cfg: dict) -> str | None:
    """Only skips when the listing states a salary and its top figure is under search.min_salary."""
    floor = cfg["search"].get("min_salary")
    if not floor:
        return None
    amounts = [int(a) * 1000 + (int(b) if b else 0) for a, b, _ in SALARY.findall(job.description)]
    amounts = [a for a in amounts if a >= 15000]          # ignore day rates and small allowances
    if amounts and max(amounts) < floor:
        return f"salary below minimum: listing says up to £{max(amounts):,}"
    return None
