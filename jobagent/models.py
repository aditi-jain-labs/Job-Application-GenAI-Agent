from dataclasses import dataclass, field, asdict
import datetime as dt


@dataclass
class Job:
    source: str                 # "reed" or "adzuna"
    job_id: str
    title: str
    company: str
    location: str
    link: str
    posted: dt.date | None
    expires: dt.date | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    is_agency: bool | None = None
    snippet: str = ""           # truncated text from the search API
    description: str = ""       # full plain text description
    apply_url: str = ""         # external application link, when different from link
    contract_type: str = ""

    # Filled in by screening
    status: str = "new"         # new | skipped | eligible
    reason: str = ""
    score: int | None = None
    quals_met: list[str] = field(default_factory=list)
    quals_missed: list[str] = field(default_factory=list)
    quals_unclear: list[str] = field(default_factory=list)
    score_notes: list[str] = field(default_factory=list)
    rules_score: int | None = None
    scored_by: str = ""         # "rules" or "claude"
    score_summary: str = ""
    employer: str = ""          # real employer named in the listing (differs from company for job boards)
    hiring_manager: str = ""
    base_cv: str = ""
    via_job_board: bool = False
    missing_essentials: list[dict] = field(default_factory=list)     # [{requirement, quote}] from Claude

    @property
    def key(self) -> str:
        return f"{self.source}:{self.job_id}"

    @property
    def limit_key(self) -> str:
        """Who the per-agency weekly limit counts against."""
        return (self.employer or self.company) if self.via_job_board else self.company

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("posted", "expires"):
            d[k] = d[k].isoformat() if d[k] else None
        return d
