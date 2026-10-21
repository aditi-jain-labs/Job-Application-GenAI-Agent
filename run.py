"""Job application pipeline.

    python3 run.py --login                   open the browser to log in to Reed yourself
    python3 run.py --days 14                 first run
    python3 run.py --days 1                  daily run
    python3 run.py --days 14 --tailor 5      tailor and apply to the top 5 only
    python3 run.py --days 14 --only 123 456  tailor and apply to these job ids only
    python3 run.py --mark-applied 123        record a job you applied to by hand
    python3 run.py --from-screening --only 123   reuse today's screening (no fetching or scoring), for tests
    python3 run.py --referrals               referral monitor only (career sites in referrals.yaml; never applies)
    python3 run.py --days 1 --referrals      daily Reed run, then the referral monitor
    python3 run.py --ats                     company boards on Greenhouse, Lever and Ashby only (ats_companies.yaml)
    python3 run.py --days 7 --ats --dry-run  the same for the last 7 days, as a dry run whatever config.yaml says
    python3 run.py --days 1 --referrals --ats --watch   the daily search: Reed, company boards, referrals, watch list
    python3 run.py --watch --companies Deloitte KPMG   watch list, only these companies (also works with --referrals, --ats)
    python3 run.py --job URL                 score one job and write a tailored CV (or --job description.txt --title T --company C)
    python3 run.py --pdf 123                 make the PDF CV for a referral, watch list or manual job (only the .docx is kept)
    python3 run.py --cleanup                 compress and expire screenshots, delete old screening/state and cache files

Steps: fetch, dedupe, full description, skip rules, score (rules, then Claude for jobs at
llm.rule_score_min_for_llm or above), tailored CV (and cover letter if documents.write_cover_letters),
apply with Playwright (dry_run in config.yaml fills forms and screenshots without submitting),
report and applied.csv.
Results go to output/YYYY-MM-DD/: screening.md, tailoring.md, report.md, the documents and screenshots.
Every run ends with --cleanup.
"""
import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

import requests

from jobagent import (addjob, ats, byhand, checks, claude_score, cleanup, details, ledger, llm, referrals, report, review, rules, scoring,
                      progress, sources, tailor, unanswered, watch)
from jobagent.models import Job
from jobagent.settings import CACHE_DIR, env, llm_settings, load_config, run_dir, today

LAST_RUN = CACHE_DIR / "last_run.json"

OFF_TARGET = "title is not a target role"
md = report.md


def write_screening(jobs, dropped, cfg, days, fetched) -> tuple:
    out = run_dir()
    threshold = cfg["run"]["match_threshold"]
    eligible = sorted([j for j in jobs if j.status == "eligible"], key=lambda j: -j.score)
    off_target = [j for j in jobs if j.reason == OFF_TARGET]
    skipped = sorted([j for j in jobs if j.status == "skipped" and j.reason != OFF_TARGET],
                     key=lambda j: -(j.score or -1))

    lines = [
        f"# Screening {today().isoformat()}",
        "",
        f"Last {days} days. Fetched {fetched}, duplicates removed {len(dropped)}, screened {len(jobs)}, "
        f"off target titles {len(off_target)}. Above threshold ({threshold}): {len(eligible)}. "
        f"Skipped: {len(skipped)}.",
        "",
        "## Above threshold",
        "",
        "| Score | Rules | Job title | Posted by | Limit counts against | Agency | Base CV | Link |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for j in eligible:
        poster = f"{j.company} (job board)" if j.via_job_board else j.company
        lines.append(f"| {j.score} | {j.rules_score} | {md(j.title)} | {md(poster)} | {md(j.limit_key)} | "
                     f"{'yes' if j.is_agency else 'no'} | {j.base_cv} | {j.link} |")
    lines += ["", "## Skipped", "", "| Score | Rules | Job title | Company | Reason | Link |", "|---|---|---|---|---|---|"]
    for j in skipped:
        lines.append(f"| {md(j.score)} | {md(j.rules_score)} | {md(j.title)} | {md(j.company)} | "
                     f"{md(j.reason)} | {j.link} |")
    if dropped:
        lines += ["", "## Duplicates removed", "", "| Job title | Company | Reason |", "|---|---|---|"]
        for j in dropped:
            lines.append(f"| {md(j.title)} | {md(j.company)} | {j.reason} |")

    if off_target:
        lines += ["", f"## Off target titles ({len(off_target)})", "",
                  "Reed keyword search also matches description text, so these came back. Not scored.", ""]
        lines.append(", ".join(sorted({md(j.title) for j in off_target})))

    lines += ["", "## Qualification detail (above threshold)", ""]
    for j in eligible:
        lines += [f"### {j.title}, {j.company} ({j.score}, scored by {j.scored_by}; rules gave {j.rules_score})", "", j.link, ""]
        if j.score_summary:
            lines += [j.score_summary, ""]
        if j.employer:
            lines.append(f"- employer named in listing: {j.employer}")
        if j.hiring_manager:
            lines.append(f"- hiring manager: {j.hiring_manager}")
        lines += [f"- {n}" for n in j.score_notes]
        lines += ["", "**Met**", ""] + [f"- {q}" for q in j.quals_met or ["(none extracted)"]]
        lines += ["", "**Missed**", ""] + [f"- {q}" for q in j.quals_missed or ["(none)"]]
        if j.quals_unclear:
            lines += ["", "**Unclear**", ""] + [f"- {q}" for q in j.quals_unclear]
        lines.append("")

    (out / "screening.md").write_text("\n".join(lines))
    (out / "screening.json").write_text(json.dumps([j.to_dict() for j in jobs + dropped], indent=1))
    return out, eligible, skipped


def screen(cfg, days, limit=None):
    """Steps 1 to 5. Returns (jobs screened, duplicates dropped, fetched count)."""
    threshold = cfg["run"]["match_threshold"]
    print(f"Fetching jobs from the last {days} days...")
    progress.say("Searching Reed for new jobs")
    jobs = sources.fetch_all(cfg, days)
    fetched = len(jobs)
    print(f"  {fetched} jobs found")

    job_boards = cfg["run"].get("job_boards") or cfg.get("job_boards") or []
    jobs, dropped = rules.dedupe(jobs, fuzzy=cfg["run"].get("fuzzy_title_duplicates", True), job_boards=job_boards)
    print(f"  {len(dropped)} duplicates removed, {len(jobs)} left")
    if limit:
        jobs = jobs[:limit]

    session = requests.Session()
    session.auth = (env("REED_API_KEY"), "") if cfg["sources"].get("reed") else None
    for i, job in enumerate(jobs, 1):
        if progress.stop_requested():
            job.status, job.reason = "deferred", progress.STOPPED
            continue
        # Cheap title check first, so we don't fetch descriptions we will never use
        if reason := rules.title_excluded(job, cfg):
            job.status, job.reason = "skipped", reason
            continue
        if not scoring.is_target_title(job.title, job.snippet):
            job.status, job.reason = "skipped", OFF_TARGET
            continue
        progress.say(f"Reading job descriptions, {i} of {len(jobs)}")
        try:
            details.fetch_description(job, session)
        except details.PageMissing as e:
            job.status, job.reason = "skipped", str(e)
            continue
        except requests.RequestException as e:
            job.status, job.reason = "skipped", f"page did not load: {e.__class__.__name__}"
            continue
        if reason := rules.skip_reason(job, cfg):
            job.status, job.reason = "skipped", reason
            continue
        scoring.score(job)
        job.rules_score, job.scored_by = job.score, "rules"
        job.base_cv = scoring.base_cv_for(job.title)
        claude_score.mark_job_board(job, job_boards)
        if i % 100 == 0:
            print(f"  screened {i}/{len(jobs)}")

    settings = llm_settings(cfg)
    candidates = sorted([j for j in jobs if j.status == "new" and j.score >= settings["score_from"]], key=lambda j: -j.score)
    print(f"  {len(candidates)} jobs scored {settings['score_from']}+ on the rules; scoring with Claude ({settings['score_model']})...")
    if settings["max_scores"] and len(candidates) > settings["max_scores"]:
        for job in candidates[settings["max_scores"]:]:
            job.score_notes.insert(0, f"not sent to Claude: max_llm_scores_per_run ({settings['max_scores']}) reached; rule score kept")
        print(f"  only the top {settings['max_scores']} go to Claude (max_llm_scores_per_run)")
        candidates = candidates[:settings["max_scores"]]
    claude_score.score_all(candidates, cfg)
    for job in jobs:
        scoring.check_line_management(job)
        if job.status != "new":
            continue
        if job.score < (limit := scoring.threshold_for(job.title, cfg)):
            job.status, job.reason = "skipped", f"match score {job.score} below threshold {limit}"
        else:
            job.status = "eligible"
    return jobs, dropped, fetched


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, help="look back this many days (14 first run, 1 daily)")
    ap.add_argument("--limit", type=int, help="screen at most this many jobs (for testing)")
    ap.add_argument("--tailor", type=int, help="tailor and apply to at most this many top jobs (default: daily_cap)")
    ap.add_argument("--only", nargs="+", metavar="JOB_ID", help="tailor and apply to these job ids only")
    ap.add_argument("--no-apply", action="store_true", help="stop after tailoring")
    ap.add_argument("--login", action="store_true", help="open the browser so you can log in to Reed yourself")
    ap.add_argument("--mark-applied", nargs="+", metavar="JOB_ID", help="record jobs you applied to by hand")
    ap.add_argument("--from-screening", action="store_true", help="reuse today's screening.json instead of fetching and scoring")
    ap.add_argument("--ats", action="store_true",
                    help="check the Greenhouse, Lever and Ashby boards in ats_companies.yaml (--days sets the window); "
                         "on its own it skips Reed; with --days N --referrals it runs after Reed")
    ap.add_argument("--dry-run", action="store_true", help="dry run for this run, whatever run.dry_run in config.yaml says")
    ap.add_argument("--referrals", action="store_true",
                    help="check the career sites in referrals.yaml (on its own, or after the Reed run when --days is given)")
    ap.add_argument("--pdf", nargs="+", metavar="JOB_ID", help="make the PDF CV from the saved .docx for these jobs")
    ap.add_argument("--cleanup", action="store_true", help="screenshots and old files only (also runs after every run)")
    ap.add_argument("--watch", action="store_true", help="check the companies in watch_companies.yaml (never applies)")
    ap.add_argument("--companies", nargs="+", metavar="NAME",
                    help="limit --referrals, --watch and --ats to these companies (part of the name is enough, e.g. Deloitte); "
                         "Reed is skipped")
    ap.add_argument("--job", metavar="URL_OR_FILE",
                    help="score one job and write a tailored CV for it: a job page URL, or a text file with the description")
    ap.add_argument("--title", help="with --job: the job title (when the page or text does not show it)")
    ap.add_argument("--company", help="with --job: the company")
    args = ap.parse_args()

    if args.cleanup:
        cleanup.run()
        return
    if args.pdf:
        for job_id in args.pdf:
            print(f"PDF: {tailor.pdf_for(job_id)}")
        return
    if args.login:
        from jobagent import browser
        browser.login()
        return
    if args.mark_applied:
        for job_id in args.mark_applied:
            print(f"Marked as applied: {ledger.mark_applied(job_id)}")
            for path in byhand.mark_done(job_id):
                print(f"  updated {path}")
        return
    if args.job:
        checks.preflight(load_config(), reed=False, applying=False)
        addjob.run(args.job, args.title, args.company)
        return
    started = dt.datetime.now()
    calls_before = dict(llm.CALLS)
    progress.start()
    try:
        summary = pipeline(args)
        stopped = progress.STOP.exists()
        if stopped:
            print(f"\nRun {progress.STOPPED}. Jobs not reached are left for the next run.")
        progress.say("Tidying up")
        cleanup.run()
    finally:
        progress.finish()
    LAST_RUN.write_text(json.dumps({"stopped": stopped,
        "started": started.isoformat(timespec="seconds"), "finished": dt.datetime.now().isoformat(timespec="seconds"),
        "command": "python3 run.py " + " ".join(sys.argv[1:]), **summary,
        "claude_calls": llm.CALLS["made"] - calls_before["made"], "claude_cached": llm.CALLS["cached"] - calls_before["cached"]}, indent=1))


def pipeline(args) -> dict:
    cfg = load_config()
    reed_run = not (((args.ats or args.watch or args.referrals) and not (args.days is not None and args.referrals)
                      and not (args.only or args.from_screening)) or bool(args.companies))
    checks.preflight(cfg, reed=reed_run, applying=reed_run and not args.no_apply)
    dry_run = args.dry_run or bool(cfg["run"].get("dry_run", True))
    mode = "dry_run" if dry_run else "live"
    days = args.days or cfg["run"].get("daily_days", 1)
    settings = llm_settings(cfg)
    llm.USE_CACHE = settings["cache"]
    letters = bool((cfg.get("documents") or {}).get("write_cover_letters", True))
    # Reuse a CV that already passed the checks: always when cache_llm_results is on, and in dry runs
    # when testing.reuse_existing_documents is on
    reuse = settings["cache"] or (dry_run and (cfg.get("testing") or {}).get("reuse_existing_documents"))

    # Reed runs unless only the extra sources were asked for: --referrals, --ats or --watch on their own
    # (--days N --ats is the company boards for N days). --days N --referrals [--ats] [--watch] runs Reed first.
    extras_only = ((args.ats or args.watch or args.referrals) and not (args.days is not None and args.referrals)
                   and not (args.only or args.from_screening)) or bool(args.companies)      # --companies: company sources only
    summary = {"applied": 0, "unconfirmed": 0, "dry_run_filled": 0, "ats_matches": 0, "referral_matches": 0}
    if args.companies and not (args.ats or args.watch or args.referrals):
        print("--companies limits --watch, --referrals or --ats; add one of them.")
    if not extras_only:
        summary.update(reed(args, cfg, dry_run, days, reuse, letters, mode))
    if args.ats:
        res = ats.run(cfg, args.days, dry_run, args.companies)
        summary["ats_matches"] = len(res["results"])
    if args.referrals:
        summary["referral_matches"] = len(referrals.run(cfg, args.companies)["new"])
    if args.watch:
        summary["watch_matches"] = len(watch.run(cfg, args.companies)["new"])
    summary["manual"] = byhand.ADDED["count"]
    return summary


def reed(args, cfg: dict, dry_run: bool, days: int, reuse: bool, letters: bool, mode: str) -> dict:
    """The Reed run: fetch, screen, tailor, apply, report, applied.csv."""
    settings = llm_settings(cfg)
    if args.from_screening:
        saved = json.loads((run_dir() / "screening.json").read_text())
        jobs = [Job(**{**{k: v for k, v in d.items() if k in Job.__dataclass_fields__}, "posted": None, "expires": None})
                for d in saved]
        dropped = [j for j in jobs if j.status == "duplicate"]
        jobs = [j for j in jobs if j.status != "duplicate"]
        out, eligible = run_dir(), sorted([j for j in jobs if j.status == "eligible"], key=lambda j: -j.score)
        print(f"Reusing today's screening: {len(eligible)} above threshold")
    else:
        jobs, dropped, fetched = screen(cfg, days, args.limit)
        out, eligible, skipped = write_screening(jobs, dropped, cfg, days, fetched)
        print(f"\nAbove threshold: {len(eligible)}   Skipped: {len(skipped)}")
        print(f"Screening: {out / 'screening.md'}")

    # Target roles skipped for a missing essential go on the review list
    to_review = [review.entry(j, today().isoformat()) for j in jobs
                 if j.reason.startswith("missing essential") and j.missing_essentials and scoring.is_target_title(j.title, j.description)]
    added = review.add(to_review, cfg) if to_review else 0

    state = report.load_state(out)
    if not dry_run:     # results from today's dry runs say nothing about a real run
        state["apply"] = {k: v for k, v in state["apply"].items() if v["status"] != "dry_run"}

    # 6. Tailored CV (and cover letter, if switched on)
    cap = min(x for x in [args.tailor if args.tailor is not None else cfg["run"]["daily_cap"], settings["max_tailor"]] if x is not None)
    todo = eligible
    if args.only:
        todo = [j for j in eligible if j.job_id in args.only]
        missing = set(args.only) - {j.job_id for j in todo}
        if missing:
            print(f"  not above threshold today, so not tailored: {', '.join(sorted(missing))}")
    todo = todo[:cap]

    if todo and (cfg.get("llm") or {}).get("check_apply_page_first"):
        from jobagent import apply
        print(f"\nChecking {len(todo)} job pages before tailoring...")
        gone = apply.precheck(todo, dry_run)
        for job in todo:
            if job.key in gone:
                state["apply"][job.key] = {"status": "skipped", "reason": gone[job.key], "notes": []}
                print(f"  skip {job.title} | {job.employer or job.company}: {gone[job.key]}")
        todo = [j for j in todo if j.key not in gone]

    tailored = []
    if todo:
        print(f"\nTailoring {len(todo)} CVs{' and cover letters' if letters else ''}...")
        for n, job in enumerate(todo, 1):
            if progress.stop_requested():
                break
            progress.say(f"Writing CVs, {n} of {len(todo)}")
            rec = state["tailoring"].get(job.key)
            if reuse and rec and rec["ok"] and rec.get("cv") and Path(rec["cv"]).with_suffix(".pdf").exists():
                print(f"  reuse {job.title} | {job.employer or job.company}")
                tailored.append(job)
                continue
            result = None
            for tries in range(2):
                try:
                    result = tailor.tailor(job, cfg, out, keep="pdf")
                    break
                except RuntimeError as e:   # Word did not produce the PDF: restart Word and retry once (Claude's draft is cached)
                    result = {"job": job, "ok": False, "errors": [f"{e.__class__.__name__}: {e}"]}
                    if "did not produce" in str(e) and tries == 0:
                        print(f"  retrying the PDF for {job.title}: {e}")
                        tailor._restart_word()
                        continue
                    break
                except Exception as e:      # one bad job should not stop the run
                    result = {"job": job, "ok": False, "errors": [f"{e.__class__.__name__}: {e}"]}
                    break
            state["tailoring"][job.key] = report.tailoring_record(result)
            state["apply"].pop(job.key, None)          # a fresh draft needs a fresh application
            print(f"  {'ok  ' if result['ok'] else 'FAIL'} {job.title} | {job.employer or job.company}")
            if result["ok"]:
                tailored.append(job)
        report.save_state(out, state)
        report.write_tailoring(out, state, letters)
        print(f"Review: {out / 'tailoring.md'}")

    # 7 to 9. Apply, within the limits in config.yaml
    run_keys = set()
    if tailored and not args.no_apply:
        from jobagent import apply
        print(f"\n{'Dry run: filling' if dry_run else 'Applying to'} {len(tailored)} applications...")
        results, profile = apply.apply_all([(j, state["tailoring"][j.key]) for j in tailored], cfg, out)
        state["apply"].update(results)
        run_keys |= set(results)
        for job in tailored:
            res = results.get(job.key) or {}
            if res.get("status") != "skipped":
                continue
            if q := unanswered.from_reed_reason(res["reason"]):
                unanswered.add(job, [{"label": q, "note": "Reed form"}])
            if not dry_run and not apply.GONE.match(res["reason"]):        # the CV is ready: apply on the site yourself
                byhand.add(job, str(Path(state["tailoring"][job.key]["cv"]).with_suffix(".pdf")), res["reason"])
        if profile:
            state["profile_cv"] = profile
        report.save_state(out, state)

    # 10. Report
    report.write_report(out, jobs + dropped, state, dry_run)
    print(f"Report: {out / 'report.md'}")

    # 11. applied.csv: every job seen this run. Duplicates of jobs already in applied.csv are not repeated.
    entries = []
    touched = {j.key for j in eligible if not args.only or j.job_id in args.only}
    for job in jobs + dropped:
        if args.from_screening and job.key not in touched:      # already recorded when the screening ran
            continue
        if job.reason == "already seen":
            continue
        status, reason = report.outcome(job, state)
        if status == "new":
            status, reason = "deferred", "not screened this run"
        if status == "deferred" and progress.STOP.exists():
            reason = progress.STOPPED
        entries.append({"job": job, "status": status, "reason": reason, "mode": mode})
    ledger.append(entries)
    print(f"applied.csv: {len(entries)} rows added ({mode})")
    print(f"Missing essentials review: {len(to_review)} this run, {added} new on the list "
          f"({(cfg.get('review') or {}).get('missing_essentials_file', 'output/missing_essentials.md')})")
    print(f"Claude calls: {llm.CALLS['made']} made, {llm.CALLS['cached']} answered from the cache")
    done = Counter(v["status"] for k, v in state["apply"].items() if k in run_keys)
    return {"applied": done["applied"], "unconfirmed": done["unconfirmed"], "dry_run_filled": done["dry_run"]}


if __name__ == "__main__":
    main()
