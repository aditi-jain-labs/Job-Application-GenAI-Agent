# Job application agent: rules

<!-- CHANGE THESE TO SUIT YOU. This file is the agent's rule book: Claude reads it every time it scores a job or
     writes a CV or cover letter. The CV and letter writing rules (sections 0, 3, 4 and 5) are a starting point,
     so edit them freely to match your own style. The base CV file names in section 0 must match the files you
     put in master_cvs/. Keep section 1 (truth) as it is. -->

Read this file, profile.md, projects/, answers.yaml and config.yaml before every run.

## 0. Pick the base CV
Choose the closest master CV in master_cvs/ as the starting point, then tailor it:
- cv_data_engineering.docx: data engineer, data platform, analytics engineer, big data, Spark, lakehouse roles.
- cv_ai_engineering.docx: AI engineer, LLM/GenAI engineer, forward deployed engineer, solutions engineer, ML platform roles.
- cv_data_science.docx: data scientist, ML engineer, applied scientist, analytics or modelling roles.
- cv_data_science.docx: also data analyst, insights analyst, product analyst, BI analyst and AI analyst roles. Lead with your reporting, SQL, dashboard and stakeholder insight work from profile.md.
- cv_consulting.docx: data or AI consultant, solutions architect, technical account, client delivery, life sciences consulting roles.
Keep the base CV's layout, fonts and section order. Swap, reorder and reword bullets using facts from profile.md.
Log which base CV was used in the report.

## 1. Truth (most important)
- Use only facts from profile.md and projects/. Never invent tools, numbers, titles, team sizes or dates.
- Never swap one technology for another to fit a job (e.g. Azure for AWS). Claim only the stack profile.md states, never a similar tool from another vendor.
- If the job needs a skill I don't have, leave it out. Do not imply it.
- Keep the verb as true as the fact: "integrated with APIs" must not become "built APIs"; "guided engineers" must not become "managed a team"; "contributed to" must not become "drove".
- If there is no number for an achievement, describe a concrete outcome. Never make up a number.
- Answer form questions only from answers.yaml. If a question is not covered, skip the job and log it.

## 2. Skip the job if any of these are true
- It says it cannot sponsor visas, or needs right to work without sponsorship (see phrases in config.yaml).
- It needs DV (Developed Vetting) clearance (see phrases in config.yaml). SC clearance roles and SC residency wording are allowed: I can go through SC vetting.
- The listing says it is closed or expired.
- The page does not load or shows "page not found".
- A CAPTCHA, "are you human" or other bot check appears. Never try to get past it.
- A login is needed that is not already saved in the browser profile.
- The match score is below the threshold in config.yaml.
- I clearly miss an ESSENTIAL or must have requirement (look for "essential", "required", "must have", "you must", "at least one of the following"). Examples: a cloud platform I have not used (GCP, BigQuery, Azure Data Factory, Databricks, Snowflake), a required PhD, or required years in a domain I lack (e.g. medical imaging, DICOM). Skip with reason "missing essential: X". Missing "desirable" or "nice to have" items is fine.
- Scoring must apply the same rule: any missing essential caps the score at 50.
- When the title is a target role but an essential is missing, also add the job to the review list in config.yaml (review.missing_essentials_file): job title, company, score, link, the missing requirement, and the exact line from the listing. This lets me check whether I truly lack it or forgot to add it to profile.md.
- I have already applied (it is in applied.csv).
Jobs that say nothing about sponsorship are allowed.
Always read the FULL job description before checking these rules, because API text is cut short.

## 3. CV rules (based on Google's resume guidance)
- Write every bullet as: accomplished [X] as measured by [Y], by doing [Z].
- Tie each bullet to the job's qualifications. Use the job's own terms where they are true for me.
- Show each minimum qualification clearly, near the top (summary and first bullets).
- Be specific about projects: what was the outcome and how was it measured.
- Show leadership where true: team size, scope, mentoring, knowledge transfer.
- Put the most relevant bullets first in each role.
- Up to 5 bullets for the current role, 2 to 3 for older roles.
- Include the Projects section when the job involves AI, LLMs, agents, AWS, data reliability or observability (see projects/). Pick the 1 or 2 most relevant projects from projects/. Drop it for pure consulting roles if space is tight.
- Keep it short: 2 pages maximum. UK spelling.
- Skills section: about 4 short lines, grouped by theme (e.g. Data Management, Data Engineering, Data Modelling, Cloud and Delivery). A recruiter should be able to read only the Skills section and know what I can do, so DO list the key tools and techniques even if they also appear in bullets (e.g. Glue Data Quality, SQL Server, Lake Formation). Put the job description's exact terms first. No soft skills (no "knowledge transfer", "stakeholder management", "communication"); show those in bullets instead. No minor or repeated tools just to fill space. Only list skills in profile.md.
- No first person in the CV ("I", "my", "me"). Write "Led a client project..." not "a client project I led".
- Each bullet label must describe the work in that bullet. Do not relabel work to suit the job (e.g. a Kimball BI model is not "Schema Design for ML").
- Keep the header, education and certification exactly as in profile.md.

## 4. Style rules (CV and cover letter)
- Avoid hyphenated words by rephrasing ("metadata-driven framework" becomes "framework driven by metadata"). Never just delete the hyphen and leave a broken compound like "metadata driven framework". Names and fixed terms (e.g. "AWS Certified Data Engineer – Associate", "proof of concept") stay as they are.
- No two bullets start with the same verb.
- Vary sentence structure. Do not repeat a pattern in back to back bullets.
- Avoid repeating the same word within a section.
- No AI buzzwords or generic phrases. Banned: leverage, leveraged, spearhead, passionate, synergy, dynamic, cutting-edge, seamless, robust, innovative, results-driven, team player, fast-paced, game changer, delve, harness, unlock, transformative, journey, landscape, ever-evolving, testament, elevate, empower, foster, streamline, utilise.
- No lines like "I am excited to apply" or "I believe I would be a great fit". Say something specific instead.
- Plain, direct sentences.

## 5. Cover letter
Only used when write_cover_letters is true in config.yaml (currently off, because Reed does not accept cover letters).
Goal: one short story that connects their problem to work I have done. It should read like a person wrote it, not a template.
See examples/cover_letter_example.md for the approved tone and flow. Follow its shape, never copy its sentences.

Structure (under 320 words, 4 short paragraphs):
- Paragraph 1, the hook and why them: open with THEIR need or mission, taken from the listing, in your own words. Say why this company or team appeals, using a specific detail (their product, sector, customers, a project or value they name). Then show I have faced a similar problem, with one concrete detail of scale. Never open with "I am applying for".
- Paragraph 2, the proof: tell the most relevant technical achievement as a mini story (the problem, what I did, the result with a number). Link a second achievement to it with a real connecting idea (e.g. "That work taught me...", "Earlier, at [previous employer]...") so the paragraph flows. Two achievements maximum.
- Paragraph 3, how I work: pick the 2 soft skills the listing asks for most (e.g. stakeholder management, communication, mentoring, working with clients, ownership). Show each with a real example from profile.md, never with adjectives. Good: "At [employer] I ran sessions that left the BI teams running and extending their own reporting." Bad: "I am an excellent communicator."
- Paragraph 4, the close: say what I would bring to THIS team, and offer one specific thing to discuss that follows from paragraph 2. No new facts here.

Why this company and mission:
- Use what the listing says about the company, its mission, values or product. If the employer is named and it is a direct employer, you may also read its own website About or Careers page.
- Connect the mission to something real in my background from profile.md, for example a sector I have worked in (public sector, healthcare, retail...) or a theme I have worked on (privacy, responsible AI...).
- If there is no honest link, say plainly what draws me to the role or the work instead. Never pretend a personal connection I do not have.
- Recruitment agency listing with the client unnamed: write about the client's sector and the role as described. Do not guess the client's name.

Soft skills:
- Only claim soft skills that profile.md backs up with a real example (for instance stakeholder and client work, explaining technical choices to non-technical leaders, knowledge transfer, guiding other engineers, taking a product from proof of concept to production).
- Use the listing's own wording for the skill where it is true, but show it through the example.
Flow rules:
- Each paragraph must follow from the one before. Read it aloud: if a paragraph could be moved without anything breaking, rewrite it.
- Never describe the letter or the listing: banned phrases include "the listing says", "the job description mentions", "this matches", "matches your requirements", "two pieces of work", "as you can see", "I believe I am a strong fit".
- Do not say "match", "matches" or "aligns" at all.
- Vary sentence length. Mix short and longer sentences; no more than two sentences in a row starting with "I".
- Every claim must come from profile.md exactly. Do not upgrade facts (e.g. "other engineers" must not become "junior engineers"; "led design" must not become "managed a team").

Format:
- Header: same as the CV header. Then the date.
- Who to address, in this order:
  1. The hiring manager, if the listing names one.
  2. Otherwise the recruiter or contact person, if named (in the listing text, the Reed contact field, or the "contact" or "apply to" line).
  3. Otherwise "Dear Hiring Manager,".
- With a name, use "Dear [First name] [Surname]," (no Mr/Ms unless the listing uses a title).
- Always sign off "Warm regards," followed by my name (candidate.candidate_name in config/config.yaml) on the next line, whoever the letter is addressed to.
- If writing to an agency recruiter, the content still talks about the client role and team, not the agency.
- Log which name was used, and where it came from, in the report.
- After writing, check it against sections 1 and 4 and fix any issue before saving.

## 6. Output
- Save to output/YYYY-MM-DD/ as Company_Role_CV and Company_Role_CoverLetter, in .docx and .pdf.
- Daily report at output/YYYY-MM-DD/report.md with two tables:
  - Applied: job title, company, match score, link
  - Not applied: job title, company, match score, link, reason
- Add every job seen to applied.csv with its status.
