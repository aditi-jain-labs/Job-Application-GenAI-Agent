"""Start-of-run check for a fresh copy: says which template file still needs filling in, before any work starts."""
import os

import yaml

from .settings import ANSWERS_PATH, MASTER_CVS_DIR, PROFILE_PATH, REFERRALS_PATH, filled_in


def problems(cfg: dict, reed: bool, applying: bool) -> list[str]:
    out = []
    if not PROFILE_PATH.exists() or not filled_in(PROFILE_PATH.read_text()):
        out.append("profile.md is empty: fill in your experience, skills and education.")
    if not str((cfg.get("candidate") or {}).get("candidate_name") or "").strip():
        out.append("config/config.yaml: candidate.candidate_name is empty: put your full name there.")
    if not list(MASTER_CVS_DIR.glob("*.docx")):
        out.append("master_cvs/ has no CVs: add your base CVs as .docx files (see master_cvs/README.md).")
    if reed and not os.environ.get("REED_API_KEY", "").strip():
        out.append(".env: REED_API_KEY is empty: add your Reed API key.")
    if reed and applying:
        answers = yaml.safe_load(ANSWERS_PATH.read_text()) if ANSWERS_PATH.exists() else None
        if not any(v not in (None, "", []) for s in (answers or {}).values() if isinstance(s, dict) for v in s.values()):
            out.append("config/answers.yaml is empty: fill in your answers to common form questions.")
    return out


def warnings() -> list[str]:
    ref = yaml.safe_load(REFERRALS_PATH.read_text()) if REFERRALS_PATH.exists() else None
    return [] if (ref or {}).get("companies") else ["config/referrals.yaml has no companies yet (only needed for --referrals)."]


def preflight(cfg: dict, reed: bool, applying: bool) -> None:
    found = problems(cfg, reed, applying)
    if found:
        raise SystemExit("Before the first run, fill in these files:\n" + "\n".join(f"  - {p}" for p in found)
                         + "\nSee README.md, section Setup.")
