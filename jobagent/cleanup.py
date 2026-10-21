"""run.py --cleanup, also run at the end of every daily run.

- Screenshots: any PNG left over is converted to compressed JPEG. _Submitted (applied) and _Unconfirmed
  screenshots are kept; every other screenshot is deleted once it is 7 days old.
- screening.json and state.json in output/ are deleted after 14 days (report.md files are kept).
- Files in cache/ are deleted after 14 days, except the stores the agent needs to remember what it has seen.
"""
import os
import time
from pathlib import Path

from .settings import CACHE_DIR, OUTPUT_DIR

SCREENSHOT_DAYS = 7
STATE_DAYS = 14
CACHE_DAYS = 14
KEEP_SHOTS = ("_Submitted.jpg", "_Unconfirmed.jpg")
# Never deleted for age: what has been seen, matched or reviewed, and the LibreOffice profile
CACHE_KEEP = {"seen_jobs.csv", "referrals_seen.json", "referral_jobs.json", "missing_essentials.json", "documents.json",
              "apply_by_hand.json", "unanswered_questions.json", "last_run.json",
              "ats_matches.json", "watch_seen.json"}
CACHE_KEEP_DIRS = {"libreoffice_profile", "claude_cwd"}


def _is_screenshot(p: Path) -> bool:
    return p.suffix.lower() in (".png", ".jpg", ".jpeg") and "uploads" not in p.parts


def _age_days(p: Path) -> float:
    return (time.time() - p.stat().st_mtime) / 86400


def to_jpeg(png: Path, quality: int = 55) -> Path:
    from PIL import Image
    jpg = png.with_suffix(".jpg")
    with Image.open(png) as im:
        im.convert("RGB").save(jpg, "JPEG", quality=quality, optimize=True)
    stat = png.stat()
    png.unlink()
    os.utime(jpg, (stat.st_atime, stat.st_mtime))      # keep the original age for the 7 day rule
    for ref in [*png.parent.glob("*.md"), *png.parent.glob("*.json")]:      # links in report.md and state.json
        text = ref.read_text()
        if png.name in text:
            ref.write_text(text.replace(png.name, jpg.name))
    return jpg


def run() -> dict:
    stats = {"converted": 0, "screenshots": 0, "state": 0, "cache": 0, "freed_kb": 0}

    def drop(p: Path, key: str) -> None:
        stats["freed_kb"] += p.stat().st_size // 1024
        p.unlink()
        stats[key] += 1

    for p in sorted(OUTPUT_DIR.rglob("*.png")) + sorted(CACHE_DIR.rglob("*.png")):
        if _is_screenshot(p):
            to_jpeg(p)
            stats["converted"] += 1
    for p in OUTPUT_DIR.rglob("*.jpg"):
        if _is_screenshot(p) and not p.name.endswith(KEEP_SHOTS) and _age_days(p) > SCREENSHOT_DAYS:
            drop(p, "screenshots")
    for p in [*OUTPUT_DIR.rglob("screening.json"), *OUTPUT_DIR.rglob("state.json")]:
        if _age_days(p) > STATE_DAYS:
            drop(p, "state")
    for p in CACHE_DIR.rglob("*"):
        if (p.is_file() and p.name not in CACHE_KEEP and not CACHE_KEEP_DIRS & set(p.relative_to(CACHE_DIR).parts)
                and _age_days(p) > CACHE_DAYS):
            drop(p, "cache")
    for d in sorted(CACHE_DIR.rglob("*"), key=lambda d: -len(d.parts)):      # empty folders left behind
        if d.is_dir() and not any(d.iterdir()) and d.name not in CACHE_KEEP_DIRS:
            d.rmdir()
    print(f"Cleanup: {stats['converted']} PNG screenshots made JPEG, {stats['screenshots']} old screenshots, "
          f"{stats['state']} old screening/state files and {stats['cache']} old cache files deleted "
          f"({stats['freed_kb'] / 1024:.1f} MB freed)")
    return stats
