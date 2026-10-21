"""Local dashboard for the job agent. Start it with ./start_dashboard.sh (http://localhost:8501).

It only starts run.py (in the Python that runs the agent, JOB_AGENT_PYTHON) and reads the files the agent
writes, so every command keeps working from the terminal as before. One run at a time: a run started here
or in a terminal blocks the start buttons until it ends. Stop asks the agent to stop at its next safe point
(see jobagent/progress.py); it never kills a run in the middle of an application.
"""
import datetime as dt
import html
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st
from ruamel.yaml import YAML

from jobagent import byhand, ledger

ROOT = Path(__file__).resolve().parent
AGENT_PY = os.environ.get("JOB_AGENT_PYTHON") or shutil.which("python3") or "python3"
CACHE = ROOT / "cache"
RUNS = CACHE / "dashboard_runs"
RUN_STATE = CACHE / "dashboard_run.json"
PROGRESS = CACHE / "progress.json"
STOP = CACHE / "stop_requested"
CONFIG = ROOT / "config" / "config.yaml"
BACKUPS = ROOT / "backups"
ATS_MATCHES = CACHE / "ats_matches.json"

KINDS = {
    "reed": {"name": "Reed", "args": ["--days", "1"], "button": "Start applying",
             "intro": "Searches Reed for new jobs, writes a tailored CV for each good match and **applies for you**."},
    "ats": {"name": "Company boards", "args": ["--ats"], "button": "Start looking",
            "intro": "Checks the job boards of the companies on your list and writes a CV for each good match. "
                     "**Finds jobs, you apply.**"},
    "referrals": {"name": "Referrals", "args": ["--referrals"], "button": "Start looking",
                  "intro": "Checks the career sites of companies where you can get a referral and writes a CV for each good match. "
                           "**Finds jobs, you apply** once you have asked for the referral."},
    "watch": {"name": "Watch list", "args": ["--watch"], "button": "Start looking",
              "intro": "Checks the big companies you are watching (PwC, Deloitte and KPMG so far) and writes a CV for each "
                       "good match. **Finds jobs, you apply.**"},
}
WATCH_READY = True

st.set_page_config(page_title="Job agent", page_icon="📋", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
#MainMenu, footer, .stAppDeployButton, [data-testid="stMainMenu"], [data-testid="stToolbarActions"],
[data-testid="stStatusWidget"], [data-testid="stDecoration"], [data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"] {display: none !important;}
.block-container {max-width: 1300px; padding-top: 4.5rem; padding-bottom: 3rem;}
h1 {font-size: 2.1rem !important;} h2 {font-size: 1.6rem !important;} h3 {font-size: 1.3rem !important;}
.muted {color: #6b7280;}
.progress {font-size: 1.15rem; padding: 0.7rem 1rem; border-radius: 0.5rem; background: #eef4ff; margin: 0.6rem 0 1rem;}
.progress.idle {background: #f4f5f7;}
.cell {overflow-wrap: break-word; line-height: 1.35;}
.head {font-weight: 600; color: #4b5563; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em;}
.row-rule {border-top: 1px solid #e5e7eb; margin: 0.35rem 0 0.5rem;}
table {width: 100% !important; table-layout: auto;}
td, th {white-space: normal !important; overflow-wrap: break-word; word-break: normal; vertical-align: top;}
[data-testid="stMarkdownContainer"] table td, [data-testid="stMarkdownContainer"] table th {font-size: 0.95rem;}
</style>""", unsafe_allow_html=True)


# ---------- runs ----------

def running_pids() -> list[int]:
    """Any run.py process, from here or a terminal."""
    out = subprocess.run(["pgrep", "-f", r"[ /]run\.py( |$)"], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def last_run(kind: str) -> dict:
    return _json(RUNS / f"last_{kind}.json")


def log_text(info: dict) -> str:
    p = Path(info.get("log", "")) if info else None
    return p.read_text(errors="replace") if p and p.is_file() else ""


def finished(text: str):
    return re.search(r"\[finished with exit code (\d+)\]", text)


def current() -> dict:
    """{"kind": ...} for a run started here that is still going; {"kind": None} for one started in a terminal; {} if idle."""
    if not running_pids():
        return {}
    state = _json(RUN_STATE)
    if state and not finished(log_text(state)):
        return state
    return {"kind": None}


def start_run(kind: str, args: list[str]) -> bool:
    if running_pids():
        st.warning("Another run is going. Wait for it to finish.")
        return False
    RUNS.mkdir(parents=True, exist_ok=True)
    STOP.unlink(missing_ok=True)
    started = dt.datetime.now()
    log = RUNS / f"{kind}_{started:%Y-%m-%d_%H%M%S}.log"
    cmd = " ".join(shlex.quote(a) for a in [AGENT_PY, "-u", "run.py", *args])
    log.write_text(f"$ python3 run.py {' '.join(shlex.quote(a) for a in args)}\n")
    # The shell writes the exit code at the end of the log; a new session keeps the run going if the page reloads
    subprocess.Popen(["/bin/sh", "-c", f"{cmd} >> {shlex.quote(str(log))} 2>&1; "
                                       f"echo \"\n[finished with exit code $?]\" >> {shlex.quote(str(log))}"],
                     cwd=ROOT, start_new_session=True)
    info = {"kind": kind, "log": str(log), "started": started.isoformat(timespec="seconds")}
    RUN_STATE.write_text(json.dumps(info))
    (RUNS / f"last_{kind}.json").write_text(json.dumps(info))
    return True


def when(iso: str) -> str:
    d = dt.datetime.fromisoformat(iso)
    return f"today at {d:%H:%M}" if d.date() == dt.date.today() else f"{d:%a %-d %b} at {d:%H:%M}"


def outcome_line(kind: str) -> str:
    info = last_run(kind)
    if not info:
        return "Not run from the dashboard yet."
    text = log_text(info)
    end = finished(text)
    if not end:
        return f"Last started {when(info['started'])}."
    if "stopped by user" in text:
        return f"Last run {when(info['started'])}: stopped by you."
    if end.group(1) != "0":
        return f"Last run {when(info['started'])} ran into a problem. Open Show details at the bottom for the reason."
    return f"Last run {when(info['started'])}: finished."


@st.fragment(run_every=2)
def controls(kind: str) -> None:
    spec = KINDS[kind]
    now = current()
    mine = now.get("kind") == kind
    busy = bool(now)
    key_was = f"was_running_{kind}"
    if st.session_state.get(key_was) and not mine:
        st.session_state[key_was] = False
        st.rerun(scope="app")           # the run ended: refresh the job list
    st.session_state[key_was] = mine

    cfg = YAML(typ="safe").load(CONFIG.read_text())
    live = not cfg["run"].get("dry_run", True)
    practice = False
    if kind == "reed":
        practice = st.toggle("Practice run: fill in the forms but send nothing", key="practice", disabled=busy)
    ready = kind != "watch" or WATCH_READY
    c1, c2, _ = st.columns([1.3, 1, 3])
    if c1.button("Start practice run" if practice else spec["button"], type="primary", disabled=busy or not ready,
                 key=f"start_{kind}", width="stretch"):
        if kind == "reed" and live and not practice:
            st.session_state[f"confirm_start_{kind}"] = True
        elif start_run(kind, spec["args"] + (["--dry-run"] if practice else [])):
            st.rerun(scope="app")
    if c2.button("Stop", disabled=not mine or STOP.exists(), key=f"stop_{kind}", width="stretch"):
        st.session_state[f"confirm_stop_{kind}"] = True

    if st.session_state.get(f"confirm_start_{kind}") and not busy:
        st.warning("This sends real applications on Reed for every good match. Start?")
        a, b, _ = st.columns([1.3, 1, 3])
        if a.button("Yes, start applying", type="primary", key=f"yes_start_{kind}", width="stretch"):
            st.session_state[f"confirm_start_{kind}"] = False
            if start_run(kind, spec["args"]):
                st.rerun(scope="app")
        if b.button("Cancel", key=f"no_start_{kind}", width="stretch"):
            st.session_state[f"confirm_start_{kind}"] = False
            st.rerun(scope="fragment")
    if st.session_state.get(f"confirm_stop_{kind}") and mine and not STOP.exists():
        st.warning("Stop this run? Anything already started finishes first (an application in progress is never cut off), "
                   "and your Reed profile CV is put back.")
        a, b, _ = st.columns([1.3, 1, 3])
        if a.button("Yes, stop", type="primary", key=f"yes_stop_{kind}", width="stretch"):
            STOP.write_text(dt.datetime.now().isoformat())
            st.session_state[f"confirm_stop_{kind}"] = False
            st.rerun(scope="fragment")
        if b.button("Keep going", key=f"no_stop_{kind}", width="stretch"):
            st.session_state[f"confirm_stop_{kind}"] = False
            st.rerun(scope="fragment")

    if mine:
        text = "Stopping after the current step" if STOP.exists() else (_json(PROGRESS).get("text") or "Starting")
        st.markdown(f"<div class='progress'>{html.escape(text)}…</div>", unsafe_allow_html=True)
    elif busy:
        other = KINDS.get(now.get("kind") or "", {}).get("name") or ("Add a job" if now.get("kind") == "addjob" else "")
        what = f"The {other} run is going" if other else "A run started outside the dashboard is going"
        text = _json(PROGRESS).get("text")
        st.markdown(f"<div class='progress idle'>{what}{': ' + html.escape(text) if text else ''}. "
                    "Wait for it to finish.</div>", unsafe_allow_html=True)
    elif not ready:
        st.markdown("<div class='progress idle'>The watch list check is not set up yet, so there is nothing to start.</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='progress idle'>{outcome_line(kind)}</div>", unsafe_allow_html=True)


def show_details(kind: str) -> None:
    st.write("")
    with st.expander("Show details", expanded=False):
        text = log_text(last_run(kind))
        if text:
            st.code("\n".join(text.splitlines()[-300:]), language=None)
        else:
            st.write("No run from the dashboard yet.")


# ---------- job tables ----------

def _abs(path: str):
    if not path:
        return None
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _open(path: Path) -> None:
    subprocess.run(["open", str(path)], check=False)


def _date(value) -> str:
    try:
        return f"{dt.date.fromisoformat(str(value)[:10]):%-d %b}"
    except ValueError:
        return ""


PLAIN = [
    ("external application", "Apply on the employer's site"),
    ("no submit button", "The form could not be sent; apply on the site"),
    ("login", "Needs a login; apply on the site"),
    ("bot check", "The site has a bot check; apply on the site"),
    ("question not covered", "A form question is not in your answers"),
    ("answers.yaml has no", "A form question is not in your answers"),
    ("unanswered questions", "Form questions not in your answers"),
    ("cover letter", "The form needs a cover letter"),
    ("wrong cv attached", "The CV upload did not work; apply on the site"),
    ("cv upload", "The CV upload did not work; apply on the site"),
    ("already applied", "Already applied"),
    ("listing closed", "Listing closed"),
    ("page not found", "Listing gone"),
    ("page failed to load", "Listing would not load"),
    ("daily cap", "Daily limit reached; next run"),
    ("applications this week", "Weekly limit for this agency reached"),
    ("outside active hours", "Outside your applying hours; next run"),
    ("stopped by user", "Stopped by you; next run"),
    ("not applied this run", "Waiting for the next run"),
    ("not tailored", "Waiting for the next run"),
    ("could not read the form", "Could not read the form; apply on the site"),
    ("ready to auto-apply", "All questions answered; apply on the site"),
    ("every required question", "All questions answered; apply on the site"),
    ("claude.md checks", "CV needs fixing"),
    ("no sponsorship", "Does not sponsor visas"),
    ("dv clearance", "Needs DV clearance"),
    ("missing essential", "Missing an essential requirement (see Review)"),
    ("below the threshold", "Score below your threshold"),
    ("below threshold", "Score below your threshold"),
]


def plain_reason(reason: str, fallback: str = "Not applied") -> str:
    r = (reason or "").lower()
    return next((text for key, text in PLAIN if key in r), fallback)


def _tick_applied(job_id: str, box_key: str) -> None:
    if st.session_state[box_key]:
        try:
            ledger.mark_applied(job_id)
            byhand.mark_done(job_id)
        except SystemExit:
            st.session_state[box_key] = False
            st.toast("Could not record this job as applied.")
    else:
        ledger.unmark_applied(job_id)
        byhand.unmark_done(job_id)


def _tick_asked(link: str, box_key: str) -> None:
    byhand.set_referral_asked(link, st.session_state[box_key])


def job_table(rows: list[dict], ticks: str = "") -> None:
    """rows: job_id, company, title, score, posted, link, cv, status (+ asked, applied, can_tick).
    ticks: "referral" adds Referral asked and Applied boxes; "applied" adds an Applied box where a job is yours to apply to."""
    if not rows:
        st.markdown("<p class='muted'>No jobs yet.</p>", unsafe_allow_html=True)
        return
    now = current()
    reed_busy = bool(now) and now.get("kind") in ("reed", None)
    widths = [2, 3.4, 0.8, 1, 0.8, 1.3, 2.6]
    cols = st.columns(widths, vertical_alignment="bottom")
    for c, h in zip(cols, ["Company", "Job", "Score", "Posted", "Link", "CV", "Status"]):
        c.markdown(f"<span class='head'>{h}</span>", unsafe_allow_html=True)
    for i, r in enumerate(rows):
        jid = str(r["job_id"])
        st.markdown("<div class='row-rule'></div>", unsafe_allow_html=True)
        c = st.columns(widths, vertical_alignment="center")
        c[0].markdown(f"<div class='cell'>{html.escape(str(r['company']))}</div>", unsafe_allow_html=True)
        extra = " · ".join(x for x in (r.get("grade"), r.get("location")) if x)
        c[1].markdown(f"<div class='cell'><b>{html.escape(str(r['title']))}</b>"
                      + (f"<br><span class='muted'>{html.escape(extra)}</span>" if extra else "") + "</div>", unsafe_allow_html=True)
        c[2].markdown(f"<div class='cell'>{r['score'] if str(r.get('score') or '') else ''}</div>", unsafe_allow_html=True)
        c[3].markdown(f"<div class='cell'>{_date(r.get('posted', ''))}</div>", unsafe_allow_html=True)
        if r.get("link"):
            c[4].markdown(f"<div class='cell'><a href='{html.escape(r['link'])}' target='_blank'>Open</a></div>",
                          unsafe_allow_html=True)
        cv = _abs(r.get("cv", ""))
        pdf = cv.with_suffix(".pdf") if cv else None
        target = pdf if pdf and pdf.exists() else cv
        if c[5].button("Open CV", key=f"cv_{ticks}_{i}_{jid}", disabled=not (target and target.exists())):
            _open(target)
        c[6].markdown(f"<div class='cell'>{html.escape(r['status'])}</div>", unsafe_allow_html=True)
        if ticks == "referral":
            k = f"asked_{jid}"
            c[6].checkbox("Referral asked", value=bool(r.get("asked")), key=k, on_change=_tick_asked, args=(r["link"], k))
        if ticks == "referral" or (ticks == "applied" and r.get("can_tick")):
            k = f"applied_{ticks}_{jid}"
            c[6].checkbox("Applied", value=bool(r.get("applied")), key=k, on_change=_tick_applied, args=(jid, k),
                          disabled=reed_busy, help="Wait for the Reed run to finish" if reed_busy else None)


@st.cache_data(show_spinner=False)
def _posted_dates(path: str, mtime: float) -> dict:
    return {f"{d['source']}:{d['job_id']}": d.get("posted") or "" for d in json.loads(Path(path).read_text())}


def reed_rows() -> list[dict]:
    done = {r["job_id"]: r for r in ledger.rows()}
    queue = byhand._load(byhand.QUEUE, {})
    rows, seen = [], set()
    for state_path in sorted((ROOT / "output").glob("2*/state.json"), reverse=True):
        state = _json(state_path)
        screening = state_path.with_name("screening.json")
        posted = _posted_dates(str(screening), screening.stat().st_mtime) if screening.exists() else {}
        for key, t in state.get("tailoring", {}).items():
            if key in seen or not key.startswith("reed:"):
                continue
            seen.add(key)
            j, a = t["job"], state.get("apply", {}).get(key) or {}
            row = done.get(j["job_id"])
            if row and row["status"] == "applied":
                status = "Applied (by you)" if row["reason"].startswith("applied by hand") else "Applied"
            elif row and row["status"] == "unconfirmed":
                status = "Sent, but Reed did not confirm it. Check Reed's Jobs applied for"
            elif a.get("status") == "dry_run":
                status = "Practice run: form filled, nothing sent"
            elif not t.get("ok"):
                status = "CV needs fixing"
            elif a:
                status = plain_reason(a.get("reason", ""))
            else:
                status = "Waiting for the next run"
            rows.append({"job_id": j["job_id"], "company": j.get("employer") or j["company"], "title": j["title"],
                         "score": j.get("score"), "posted": posted.get(key, ""), "link": j["link"], "cv": t.get("cv", ""),
                         "status": status, "applied": bool(row and row["status"] == "applied"),
                         "can_tick": key in queue or bool(row and (row["status"] == "unconfirmed"
                                                                   or row["reason"].startswith("applied by hand")))})
    return rows


def ats_rows() -> list[dict]:
    done = {r["job_id"] for r in ledger.rows() if r["status"] == "applied"}
    rows = []
    for r in sorted(_json(ATS_MATCHES).values(), key=lambda r: (r["date"], r["score"] or 0), reverse=True):
        applied = r["job_id"] in done
        if applied:
            status = "Applied (by you)"
        elif r.get("unanswered"):
            n = len(r["unanswered"])
            status = f"Apply on the site: {n} form question{'s' if n > 1 else ''} not in your answers"
        else:
            status = plain_reason(r["reason"])
        rows.append({**r, "status": status, "applied": applied, "can_tick": bool(r.get("cv"))})
    return rows


def referral_rows() -> list[dict]:
    csv_rows = {r.get("Link"): r for r in byhand._read_csv(byhand.REFERRAL_CSV)}
    rows = []
    for r in byhand._load(byhand.REFERRAL_JSON, []):
        c = csv_rows.get(r["link"], {})
        applied = bool(r.get("applied") or (c.get("Applied") or "").strip())
        asked = bool((c.get("Referral asked") or "").strip())
        status = "Applied" if applied else ("Referral asked" if asked else "Ask for a referral")
        if c.get("Note"):
            status += f" ({c['Note']})"
        rows.append({"job_id": r["job_id"], "company": r["company"], "title": r["title"], "score": r["score"],
                     "posted": r.get("posted", ""), "link": r["link"], "cv": r.get("cv", ""), "status": status,
                     "asked": asked, "applied": applied})
    return rows


def watch_rows() -> list[dict]:
    rows = []
    for r in byhand._read_csv(byhand.watch_csv()):
        applied = bool((r.get("Applied") or "").strip())
        status = "Applied" if applied else "Apply on the site"
        if r.get("Note"):
            status += f" ({r['Note']})"
        rows.append({"job_id": byhand.watch_id(r.get("Link", "")), "company": r.get("Company", ""), "title": r.get("Job title", ""),
                     "grade": r.get("Grade", ""), "location": r.get("Location", ""),
                     "score": r.get("Score", ""), "posted": r.get("Posted", ""), "link": r.get("Link", ""), "cv": r.get("CV", ""),
                     "status": status, "applied": applied, "can_tick": True})
    return rows


def added_rows() -> list[dict]:
    rows = []
    for r in byhand.everything():
        if r["kind"] != "manual":
            continue
        applied = bool((r.get("applied") or "").strip())
        note = r.get("reason") or ""
        status = "Applied" if applied else ("No CV: " + plain_reason(note, "see the note") if note.startswith("not tailored")
                                            else "Apply on the site")
        rows.append({**r, "posted": "", "status": status, "applied": applied, "can_tick": True})
    return rows


def search_box(rows: list[dict], key: str) -> list[dict]:
    q = st.text_input("Search", key=f"q_{key}", placeholder="Search company or job title", label_visibility="collapsed")
    if q:
        rows = [r for r in rows if q.lower() in f"{r['company']} {r['title']}".lower()]
    return rows


# ---------- pages ----------

def run_page(kind: str, rows_fn, ticks: str) -> None:
    spec = KINDS[kind]
    st.title(spec["name"])
    st.markdown(spec["intro"])
    controls(kind)
    st.subheader("Jobs")
    job_table(search_box(rows_fn(), kind), ticks)
    show_details(kind)


def reed_page():
    run_page("reed", reed_rows, "applied")


def ats_page():
    run_page("ats", ats_rows, "applied")


def referrals_page():
    run_page("referrals", referral_rows, "referral")


def watch_page():
    run_page("watch", watch_rows, "applied")


def _link_cell(link: str) -> str:
    return f"<a href='{html.escape(link)}' target='_blank'>Open</a>" if link else ""


def applied_page() -> None:
    st.title("Applied")
    st.markdown("Every job you have applied to, by the agent or by you, newest first.")
    df = pd.DataFrame(ledger.rows())
    if df.empty:
        st.write("Nothing yet.")
        return
    df["company"] = df["employer"].where(df["employer"] != "", df["company"])
    q = st.text_input("Search", placeholder="Search company or job title", label_visibility="collapsed")
    sent = df[df["status"] == "applied"]
    if q:
        sent = sent[(sent["company"] + " " + sent["title"]).str.lower().str.contains(q.lower(), regex=False)]
    st.markdown(f"<p class='muted'>{len(sent)} applications</p>", unsafe_allow_html=True)
    for (date, time), group in sorted(sent.groupby(["date", "time"]), key=lambda g: g[0], reverse=True):
        stamp = dt.datetime.fromisoformat(f"{date}T{time or '00:00'}")
        by_hand = group["reason"].str.startswith("applied by hand").all()
        st.subheader(f"{stamp:%a %-d %b %Y, %H:%M}" + (" · applied by you" if by_hand else ""))
        body = "".join(f"<tr><td>{html.escape(r.company)}</td><td><b>{html.escape(r.title)}</b></td>"
                       f"<td>{html.escape(str(r.match_score))}</td><td>{_link_cell(r.link)}</td></tr>"
                       for r in group.itertuples())
        st.markdown("<table><thead><tr><th style='width:28%'>Company</th><th>Job</th><th style='width:9%'>Score</th>"
                    f"<th style='width:9%'>Link</th></tr></thead><tbody>{body}</tbody></table>", unsafe_allow_html=True)
    unconfirmed = df[df["status"] == "unconfirmed"]
    if len(unconfirmed):
        st.write("")
        with st.expander(f"Sent but not confirmed ({len(unconfirmed)})"):
            st.markdown("Submit was clicked but Reed showed no confirmation. Check Reed's *Jobs applied for* page; "
                        "if a job is there, tick Applied for it on the Reed tab.")
            for r in unconfirmed.itertuples():
                st.markdown(f"- **{r.title}**, {r.company} ({_date(r.date)})" + (f" · [Open]({r.link})" if r.link else ""))


def add_job_page() -> None:
    st.title("Add a job")
    st.markdown("Score a job you found yourself and get a tailored CV for it. The same rules and scoring apply.")
    now = current()
    busy = bool(now)
    how = st.radio("From", ["Job link", "Pasted description"], horizontal=True, label_visibility="collapsed")
    title = company = link = text = url = ""
    if how == "Job link":
        url = st.text_input("Job link", placeholder="https://...")
        with st.expander("Title and company (only if the page does not show them)"):
            title = st.text_input("Job title", key="url_title")
            company = st.text_input("Company", key="url_company")
        ready = bool(re.match(r"https?://\S+$", url.strip()))
    else:
        c1, c2 = st.columns(2)
        title = c1.text_input("Job title")
        company = c2.text_input("Company")
        link = st.text_input("Link (optional)")
        text = st.text_area("Full job description", height=320)
        ready = bool(title.strip() and len(text.strip()) >= 200)
        if text and len(text.strip()) < 200:
            st.caption("Paste the full listing (at least 200 characters).")
    if st.button("Score and write CV", type="primary", disabled=busy or not ready):
        if how == "Job link":
            args = ["--job", url.strip()]
        else:
            RUNS.mkdir(parents=True, exist_ok=True)
            src = RUNS / f"job_{dt.datetime.now():%Y%m%d_%H%M%S}.txt"
            src.write_text((f"Link: {link.strip()}\n\n" if link.strip() else "") + text.strip())
            args = ["--job", str(src)]
        args += (["--title", title.strip()] if title.strip() else []) + (["--company", company.strip()] if company.strip() else [])
        if start_run("addjob", args):
            st.rerun()
    if busy and now.get("kind") != "addjob":
        st.caption("Another run is going; you can add a job when it ends.")
    add_job_result()
    st.subheader("Jobs you added")
    job_table(added_rows(), "applied")


@st.fragment(run_every=2)
def add_job_result() -> None:
    info = last_run("addjob")
    text = log_text(info)
    if not text:
        return
    if not finished(text):
        st.markdown("<div class='progress'>Reading the job, scoring it and writing the CV. This takes a minute or two…</div>",
                    unsafe_allow_html=True)
        st.session_state.addjob_running = True
        return
    if st.session_state.pop("addjob_running", False):
        st.rerun(scope="app")
    title = re.search(r"^Job: (.+?) \| (.+?) \|", text, re.M)
    score = re.search(r"^Score: (\d+)(?: \((.*)\))?$", text, re.M)
    skipped = re.search(r"^Not tailored: (.+)$", text, re.M)
    cv = re.search(r"^CV: (.+)$", text, re.M)
    note = re.search(r"^Note: (.+)$", text, re.M)
    error = re.search(r"^(.+)\n+\[finished with exit code [1-9]", text, re.M)
    st.markdown(f"**Last job added** ({when(info['started'])})")
    if title:
        st.markdown(f"{title.group(1)}" + (f", {title.group(2)}" if "not found" not in title.group(2) else ""))
    if score:
        st.markdown(f"Score: **{score.group(1)}**" + (f". {score.group(2)}" if score.group(2) else ""))
    if skipped:
        st.warning("No CV written: " + plain_reason(skipped.group(1), skipped.group(1)))
    elif cv:
        if note:
            st.info(note.group(1))
        path = _abs(cv.group(1).strip())
        if st.button("Open CV", key="addjob_cv", disabled=not (path and path.exists())):
            _open(path)
    elif error:
        st.error(error.group(1).strip())


def _table(heads: list[tuple[str, str]], rows: list[list[str]]) -> None:
    """heads: (name, width). Cells are HTML already escaped."""
    th = "".join(f"<th style='width:{w}'>{n}</th>" if w else f"<th>{n}</th>" for n, w in heads)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    st.markdown(f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>", unsafe_allow_html=True)


def review_page() -> None:
    st.title("Review")
    st.markdown("What stopped the agent: requirements you may have that are not in your profile yet, "
                "and form questions your answers do not cover.")
    esc = lambda t: html.escape(str(t or ""))
    st.subheader("Missing essentials")
    st.markdown("Good matches skipped for one requirement. If you do have it, add it to your profile and the job is "
                "scored again next time it is seen.")
    missing = _json(CACHE / "missing_essentials.json") or []
    if missing:
        _table([("Found", "8%"), ("Job", "20%"), ("Score", "7%"), ("Missing", "27%"), ("Line from the listing", ""), ("Link", "7%")],
               [[_date(r["date"]), f"<b>{esc(r['title'])}</b><br><span class='muted'>{esc(r['company'])}</span>", esc(r["score"]),
                 "<br><br>".join(esc(m["requirement"]) for m in r["missing"]),
                 "<br><br>".join(f"“{esc(m['quote'])}”" if m["quote"] else "" for m in r["missing"]), _link_cell(r["link"])]
                for r in missing])
    else:
        st.write("Nothing yet.")
    st.subheader("Form questions not in your answers")
    st.markdown("Required questions that stopped an application. Add a true answer to your answers file and those forms "
                "can be filled in automatically.")
    questions = _json(CACHE / "unanswered_questions.json") or []
    if questions:
        groups = {}
        for q in questions:
            groups.setdefault(q["question"].lower(), []).append(q)
        _table([("Question", "45%"), ("Jobs", "7%"), ("Why", "28%"), ("Example", "")],
               [[esc(g[0]["question"]), str(len(g)), esc("; ".join(dict.fromkeys(q["note"] for q in g if q["note"]))),
                 f"{esc(g[-1]['title'])}, {esc(g[-1]['company'])} · {_link_cell(g[-1]['link'])}"]
                for g in sorted(groups.values(), key=lambda g: -len(g))])
    else:
        st.write("Nothing yet.")


def settings_page() -> None:
    st.title("Settings")
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)      # the layout config.yaml uses, so only edited values change
    data = yaml.load(CONFIG.read_text())
    run, search = data["run"], data["search"]
    st.markdown("Changes apply from the next run. A copy of the old settings is kept each time you save.")
    with st.form("settings"):
        c1, c2, c3 = st.columns(3)
        cap = c1.number_input("Applications per day (most)", min_value=0, max_value=200, value=int(run["daily_cap"]))
        threshold = c2.number_input("Lowest match score to apply (0 to 100)", min_value=0, max_value=100,
                                    value=int(run["match_threshold"]))
        salary = c3.number_input("Minimum salary (£)", min_value=0, step=1000, value=int(search.get("min_salary") or 0))
        start, end = (dt.time.fromisoformat(x.strip()) for x in str(run["active_hours"]).split("-"))
        c4, c5, _ = st.columns(3)
        h_start = c4.time_input("Apply from", value=start, step=1800)
        h_end = c5.time_input("Apply until", value=end, step=1800)
        c6, c7 = st.columns(2)
        titles = c6.text_area("Job titles to search for (one per line)", "\n".join(search["titles"]), height=420)
        excludes = c7.text_area("Skip titles with these words (one per line)", "\n".join(search["exclude_title_words"]), height=420)
        saved = st.form_submit_button("Save", type="primary")
    if not saved:
        return
    new_titles = list(dict.fromkeys(t.strip() for t in titles.splitlines() if t.strip()))
    new_excludes = list(dict.fromkeys(t.strip() for t in excludes.splitlines() if t.strip()))
    if not new_titles:
        st.error("Keep at least one job title.")
        return
    if h_start >= h_end:
        st.error("'Apply from' must be before 'Apply until'.")
        return
    BACKUPS.mkdir(exist_ok=True)
    shutil.copyfile(CONFIG, BACKUPS / f"config_{dt.datetime.now():%Y%m%d_%H%M%S}.yaml")
    run["daily_cap"], run["match_threshold"], search["min_salary"] = int(cap), int(threshold), int(salary)
    run["active_hours"] = type(run["active_hours"])(f"{h_start:%H:%M}-{h_end:%H:%M}")      # keeps the quotes
    for seq, new in ((search["titles"], new_titles), (search["exclude_title_words"], new_excludes)):
        del seq[:]
        seq.extend(new)
    with open(CONFIG, "w") as f:
        yaml.dump(data, f)
    st.success("Saved. The old settings were kept as a copy.")


pages = [
    st.Page(reed_page, title="Reed", url_path="reed", default=True),
    st.Page(ats_page, title="Company boards", url_path="company-boards"),
    st.Page(referrals_page, title="Referrals", url_path="referrals"),
    st.Page(watch_page, title="Watch list", url_path="watch-list"),
    st.Page(applied_page, title="Applied", url_path="applied"),
    st.Page(add_job_page, title="Add a job", url_path="add-a-job"),
    st.Page(review_page, title="Review", url_path="review"),
    st.Page(settings_page, title="Settings", url_path="settings"),
]
st.navigation(pages, position="top").run()
