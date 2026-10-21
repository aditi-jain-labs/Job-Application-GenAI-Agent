"""Progress and stop requests, shared with the dashboard.

say("Writing CVs, 2 of 5") writes cache/progress.json, which the dashboard shows as the progress line.
The dashboard's Stop button creates cache/stop_requested. The agent only checks it at safe points: between
companies, CVs and applications, and during the waits between applications. An application that has started
always finishes, the Reed profile CV is still restored, and jobs not reached are recorded as "stopped by user".
"""
import datetime as dt
import json
import time

from .settings import CACHE_DIR

PROGRESS = CACHE_DIR / "progress.json"
STOP = CACHE_DIR / "stop_requested"
STOPPED = "stopped by user"
_announced = {"stop": False}


def say(text: str) -> None:
    try:
        PROGRESS.write_text(json.dumps({"text": text, "at": dt.datetime.now().isoformat(timespec="seconds")}))
    except OSError:
        pass


def stop_requested() -> bool:
    if STOP.exists():
        if not _announced["stop"]:
            _announced["stop"] = True
            print(f"\n{STOPPED}: finishing the current step, then stopping", flush=True)
            say("Stopping after the current step")
        return True
    return False


def sleep(seconds: float) -> bool:
    """time.sleep that ends early on a stop request. Returns False if it was cut short."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_requested():
            return False
        time.sleep(min(2.0, max(0.0, end - time.monotonic())))
    return True


def start() -> None:
    STOP.unlink(missing_ok=True)
    _announced["stop"] = False
    say("Starting")


def finish() -> None:
    STOP.unlink(missing_ok=True)
    PROGRESS.unlink(missing_ok=True)
