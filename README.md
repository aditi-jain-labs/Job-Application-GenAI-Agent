# Job agent

A personal job search assistant for the UK market. Every day it finds new jobs, checks them against your
profile, writes a tailored CV for each good match and, on Reed only, applies for you within limits you set.
Everything runs on your own computer. Claude (through the Claude Code CLI) does the scoring and the writing.

> **Warning: never push your filled-in `.env`, `profile.md`, `config/answers.yaml` or `config/referrals.yaml`.**
> They hold your API keys and personal details. Keep your working copy private, or right after cloning run:
>
> ```
> git update-index --skip-worktree .env profile.md config/answers.yaml config/referrals.yaml
> ```
>
> so git ignores your changes to these template files. Your CVs (`master_cvs/*.docx`), `projects/*.md` and
> everything the agent writes (`output/`, `cache/`, `applied.csv`) are personal too: `output/`, `cache/` and
> `applied.csv` are already in `.gitignore`.

## What it does

- **Reed:** searches the Reed API for your job titles, screens every listing, writes a tailored CV for each
  match and applies through Reed's own application form. Practice runs (`dry_run`) fill in the forms but never submit.
- **Company boards:** reads the public Greenhouse, Lever and Ashby job feeds of the companies in
  `config/ats_companies.yaml`, writes a CV for each match and checks every required form question against your
  answers. It lists these jobs for you to apply to.
- **Referrals:** checks the careers sites of companies where you can get a referral (`config/referrals.yaml`)
  and writes a CV for each match. **It never applies:** you ask for the referral first.
- **Watch list:** checks big employers' careers sites (`config/watch_companies.yaml`; readers exist for PwC UK,
  Deloitte UK and KPMG UK) and writes a CV for each match. **It never applies.**
- **Add a job:** paste a link or a description of a job you found yourself to score it and get a tailored CV.
- **Review lists:** good matches skipped for one missing requirement (so you can check whether you really lack it),
  and form questions your answers don't cover.

## How it works

```
 Reed API      company job boards      referral sites      watch list sites      a job you paste
     \                 |                     |                    |                    /
      +----------------+---------------------+--------------------+-------------------+
                                             |
                            1. title filter + skip words (cheap, no download)
                                             |
                       2. full listing: skip rules (permanent only, minimum salary,
                          no visa sponsorship, DV clearance, closed listings)
                                             |
                          3. quick rule score  --> 55+ goes to Claude
                                             |
                    4. Claude scores against profile.md (missing essentials cap it at 50)
                                             |
                              5. score >= match_threshold ?
                                             |
                   6. tailored CV from your closest base CV (rules in docs/cv_rules_for_agent.md)
                                             |
            +--------------------------------+-------------------------------+
            |                                                                |
   Reed: apply with a browser,                          everything else: listed for you
   within caps, spacing and hours                       with the CV (never applied)
            |                                                                |
            +------------------> applied.csv, reports, dashboard <-----------+
```

## Setup

You need macOS or Linux, Python 3.11+, and the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
signed in to your Claude account (the agent calls `claude -p`; no API key needed).

1. Install the agent's packages and a browser for Playwright:

   ```
   python3 -m pip install -r requirements.txt
   python3 -m playwright install chromium
   ```

2. Install LibreOffice to turn CVs into PDFs (Microsoft Word is used only if LibreOffice is missing):

   ```
   brew install --cask libreoffice
   ```

3. Fill in the template files. Each one has comments saying what to put where:

   | File | What to put there |
   |---|---|
   | `.env` | your Reed API key (free) and, optionally, Adzuna keys |
   | `profile.md` | your experience, skills, education: the only facts the CVs may use |
   | `projects/` | one file per project, copied from `projects/project_template.md` (optional) |
   | `master_cvs/` | your base CVs as .docx files (see `master_cvs/README.md`) |
   | `config/answers.yaml` | answers to common form questions (contact details, right to work, notice period...) |
   | `config/referrals.yaml` | companies where someone can refer you (optional) |
   | `config/config.yaml` | your name (`candidate.candidate_name`) and every setting marked `CHANGE THIS` |

   If a file is still empty, the agent stops and lists exactly which ones to fill in.

4. Log in to Reed once in the agent's browser (it keeps the session in `browser_profile/`):

   ```
   python3 run.py --login
   ```

## Running

```
python3 run.py --days 14                         first Reed run: the last 14 days
python3 run.py --days 1                          daily Reed run
python3 run.py --days 1 --referrals --ats --watch   the full daily search
python3 run.py --ats                             company boards only
python3 run.py --referrals                       referral companies only
python3 run.py --watch --companies Deloitte      watch list, only the named companies
python3 run.py --job https://...                 score one job and write a CV for it
python3 run.py --pdf JOB_ID                      make the PDF of a CV kept as Word only
python3 run.py --mark-applied JOB_ID             record a job you applied to yourself
python3 run.py --dry-run --days 1                practice run, whatever config.yaml says
python3 run.py --cleanup                         tidy old screenshots and cache files
```

Results go to `output/YYYY-MM-DD/` (screening, tailoring and application reports, CVs, screenshots) and the
running lists in `output/`. `applied.csv` holds real applications only; every other job seen is remembered in
`cache/seen_jobs.csv` so it is not looked at twice.

## Dashboard

A local web page for everything above: one tab per source with Start and Stop buttons and plain progress lines,
the jobs found with their CVs, an Applied history, Add a job, the review lists and the main settings.

```
python3 -m venv .dashboard-venv
.dashboard-venv/bin/pip install streamlit ruamel.yaml pandas pyyaml python-dotenv
./start_dashboard.sh
```

It opens at http://localhost:8501. Only one run goes at a time. Stop waits for a safe point: an application that
has started always finishes, and nothing is left half-submitted.

## Config files

| File | Controls |
|---|---|
| `config/config.yaml` | search titles, location, salary floor, skip words and phrases, match threshold, daily cap, applying hours, spacing, dry run, Claude models |
| `config/ats_companies.yaml` | companies checked through Greenhouse, Lever and Ashby feeds |
| `config/watch_companies.yaml` | big employers checked daily, never applied to |
| `config/referrals.yaml` | companies where you can get a referral, never applied to |
| `config/answers.yaml` | your answers to application form questions |
| `docs/cv_rules_for_agent.md` | the rules Claude follows to score jobs and write CVs and cover letters |
| `docs/examples/cover_letter_example.md` | the tone and shape for cover letters (if switched on) |

## Customise it

| To change | Edit |
|---|---|
| Job titles to search for, words that skip a title | `config/config.yaml`: `search.titles`, `search.exclude_title_words` |
| Location, minimum salary, permanent only | `config/config.yaml`: `search.location`, `search.min_salary`, `search.permanent_only` |
| How picky it is, how many a day, when it applies | `config/config.yaml`: `run.match_threshold`, `run.daily_cap`, `run.active_hours` |
| Visa sponsorship and clearance skips | `config/config.yaml`: `skip_phrases` (remove the lists that don't apply to you) |
| Your name and CV file name | `config/config.yaml`: `candidate.candidate_name`, `documents.cv_upload_name` |
| Companies checked | `config/ats_companies.yaml`, `config/watch_companies.yaml`, `config/referrals.yaml` |
| CV style, bullet format, banned words, cover letters | `docs/cv_rules_for_agent.md` (sections 0, 3, 4 and 5) |
| Base CV file names and which roles use which | `docs/cv_rules_for_agent.md` section 0, `FAMILIES` in `jobagent/scoring.py`, `BASE_CVS` in `jobagent/claude_score.py` |
| Skills the quick rule score looks for | `HAVE` and `MISSING` in `jobagent/scoring.py` (set up for an AWS data and AI engineer) |

## Safety rules

- **`dry_run: true` by default.** A new copy never submits an application: forms are filled and screenshotted,
  and the browser blocks every request that could send one. (On Reed it lets through only the CV upload it needs
  to check the file name, and puts your profile CV back afterwards.) Set `dry_run: false` only after checking
  the outputs.
- **Caps and spacing:** at most `daily_cap` applications a day, `max_per_hour` an hour and
  `max_per_agency_per_week` per agency, a random 3 to 8 minute gap between applications, and only within
  `active_hours`.
- **Never gets past a bot check.** A CAPTCHA, "are you human" page, block or a login that isn't already saved
  stops that job or site, and it is listed for you (or marked "set up a job alert"). The agent never tries to
  get around one.
- **Referral and watch list jobs are never applied to automatically**, and nor are company board jobs: you
  apply yourself with the tailored CV.
- **Truth first:** CVs and answers use only what is in `profile.md`, `projects/` and `answers.yaml`. A form
  question that isn't covered means the job is skipped and listed, never guessed.
- **One submit per job:** a job is never sent twice; if Submit is clicked but no confirmation appears, it is
  marked unconfirmed and blocked until you check it.
- **Polite reading:** careers sites are read through their public pages and job feeds only, with pauses between
  requests, and never through login or apply pages.

## Design decisions

* I designed the pipeline: find jobs, apply the rule filters, score with Claude, tailor the CV, apply or list for manual applying.
* I chose a cheap model for scoring and a stronger one for writing, with caching, which cut LLM calls by about 95%.
* I set the safety rules: spacing between applications, daily caps, never getting past bot checks, and referral jobs never auto-applied.
* I wrote the CV rules so the CV stays truthful.

Built with Claude Code as my pair programmer.

## Future plans

* Model routing: send each task to the cheapest model that can do it well.
* More job data APIs to widen the search.

## Responsible use

This is a personal tool for your own job search. You are responsible for every application it sends in your
name: read the CVs it writes, keep `dry_run` on until you trust the output, and only apply to jobs you
genuinely want and are qualified for. Respect each site's terms of use and robots rules, keep the rate limits,
and don't use it to send bulk or low-quality applications. Automated applying may be against some job boards'
terms; check before turning it on. The software comes with no warranty (see LICENSE).

## License

MIT, see [LICENSE](LICENSE).
