# Put your base CVs here

Add your own CVs as Word files (.docx). The agent copies the closest one for each job and rewrites its
bullets, keeping your layout, fonts and section order.

The default file names (docs/cv_rules_for_agent.md section 0 and jobagent/scoring.py):

| File | Used for |
|---|---|
| cv_data_engineering.docx | data engineer, data platform, analytics engineer roles |
| cv_ai_engineering.docx | AI, LLM, forward deployed and solutions engineer roles |
| cv_data_science.docx | data scientist, ML engineer and analyst roles |
| cv_consulting.docx | consultant, solutions architect and client facing roles |

You can use fewer or different files: change the names in docs/cv_rules_for_agent.md section 0, `FAMILIES` in
jobagent/scoring.py and `BASE_CVS` in jobagent/claude_score.py to match.

Each CV needs these section headings, set in a Word "Heading" style: SUMMARY, PROFESSIONAL EXPERIENCE (a role
header line followed by bullet points for each role), SKILLS, and optionally PROJECTS. Other sections (such as
EDUCATION) are kept exactly as they are.

These files are your personal data: keep them out of any public repository.
