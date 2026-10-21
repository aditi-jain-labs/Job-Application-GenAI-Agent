"""Paths, config and secrets."""
import datetime as dt
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"            # every .yaml file
DOCS_DIR = ROOT / "docs"                # CLAUDE.md (the CV and letter rules) and examples/
CONFIG_PATH = CONFIG_DIR / "config.yaml"
ANSWERS_PATH = CONFIG_DIR / "answers.yaml"
REFERRALS_PATH = CONFIG_DIR / "referrals.yaml"
ATS_COMPANIES_PATH = CONFIG_DIR / "ats_companies.yaml"
WATCH_PATH = CONFIG_DIR / "watch_companies.yaml"
RULES_PATH = DOCS_DIR / "CLAUDE.md"
EXAMPLE_LETTER = DOCS_DIR / "examples" / "cover_letter_example.md"
PROFILE_PATH = ROOT / "profile.md"
PROJECTS_DIR = ROOT / "projects"
MASTER_CVS_DIR = ROOT / "master_cvs"
APPLIED_CSV = ROOT / "applied.csv"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / "cache"

load_dotenv(ROOT / ".env")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    if not cfg:
        raise SystemExit("config/config.yaml is empty. Restore it from the repository and change the settings marked CHANGE THIS.")
    return cfg


def filled_in(text: str) -> bool:
    """False for a template that still has only headings, comments and empty bullets."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = [ln.strip() for ln in text.splitlines()]
    real = [ln for ln in lines if ln and not ln.startswith("#") and ln not in ("-", "*") and not re.fullmatch(r"[-*]\s*\[?\]?", ln)
            and not re.fullmatch(r"[-*]?\s*\w[\w ]{0,30}:\s*", ln)]
    return len(" ".join(real)) >= 80


def profile_text() -> str:
    """profile.md plus every file in projects/, with unfilled TODO lines removed."""
    if not PROFILE_PATH.exists() or not filled_in(PROFILE_PATH.read_text()):
        raise SystemExit("profile.md is empty. Fill in profile.md (your experience, skills, education) before running: "
                         "the CV writer and the scoring use only what it says.")
    parts = [PROFILE_PATH.read_text()]
    parts += [f"\n\n# File: projects/{p.name}\n\n{p.read_text()}" for p in sorted(PROJECTS_DIR.glob("*.md"))
              if p.name != "project_template.md" and filled_in(p.read_text())]
    return "\n".join(ln for ln in "".join(parts).splitlines() if "TODO" not in ln)


def candidate_name(cfg: dict | None = None) -> str:
    """config.yaml candidate.candidate_name: the cover letter sign-off and the {name} in the CV upload name."""
    name = str(((cfg or load_config()).get("candidate") or {}).get("candidate_name") or "").strip()
    if not name:
        raise SystemExit("Your name is not set. Fill in candidate.candidate_name in config/config.yaml.")
    return name


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is empty. Fill it in in the .env file (see the comments there).")
    return value


def today() -> dt.date:
    return dt.date.today()


def run_dir(date: dt.date | None = None) -> Path:
    path = OUTPUT_DIR / (date or today()).isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def llm_settings(cfg: dict) -> dict:
    """Claude settings: the llm block in config.yaml, falling back to the older claude block."""
    old, new = cfg.get("claude") or {}, cfg.get("llm") or {}
    return {
        "score_model": new.get("score_model", old.get("score_model", "sonnet")),
        "write_model": new.get("write_model", old.get("write_model", "opus")),
        "score_from": new.get("rule_score_min_for_llm", old.get("score_from", 65)),
        "max_scores": new.get("max_llm_scores_per_run"),
        "max_tailor": new.get("max_tailor_per_run"),
        "rewrites": new.get("max_rewrite_attempts", 2),
        "cache": new.get("cache_llm_results", True),
        "parallel": old.get("parallel", 4),
    }
