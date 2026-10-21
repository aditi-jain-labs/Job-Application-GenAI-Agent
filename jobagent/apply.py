"""Steps 7 to 9: fill in applications with Playwright, using the saved browser profile.

Safety rules (CLAUDE.md section 2):
- A CAPTCHA, "are you human" or other bot check skips the job straight away. Nothing on the page is touched.
- A login or account creation skips the job. The agent never types a password.
- A closed listing, a page that fails to load, or any form question not covered by answers.yaml skips the job.
- In dry run, forms are filled and screenshotted but nothing that could submit is ever clicked,
  including "Next" on multi step forms.

Reed shows the CV saved on your Reed profile in its application box. The agent clicks Update and uploads
the tailored CV under documents.cv_upload_name, then reads the file name the box shows. If it is not the
tailored file, the job is skipped ("wrong CV attached") and nothing is submitted. Uploading replaces the
CV on your Reed profile (dry runs too, so the name can be checked), so after the run the file in
documents.restore_profile_cv_after_run is put back on the profile.
"""
import datetime as dt
import random
import re
import shutil
import time
from pathlib import Path

import yaml
from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeout, sync_playwright

from . import browser, ledger, progress, tailor
from .models import Job
from .settings import ANSWERS_PATH, ROOT, candidate_name


class Skip(Exception):
    """The job cannot be applied to automatically. The message is the reason."""


BOT_FRAMES = re.compile(r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare|arkoselabs|funcaptcha|geetest|captcha", re.I)
BOT_TEXT = re.compile(r"are\s+you\s+(a\s+)?human|verify\s+(that\s+)?you\s+are\s+(a\s+)?human|i'?m\s+not\s+a\s+robot|"
                      r"\bcaptcha\b|checking\s+your\s+browser|just\s+a\s+moment\.\.\.|unusual\s+traffic|press\s+and\s+hold", re.I)
CLOSED_TEXT = re.compile(r"no\s+longer\s+(accepting|available|advertised)|(job|vacancy|role|position)\s+(has\s+)?(now\s+)?(expired|closed)|"
                         r"applications?\s+(are\s+|have\s+)?(now\s+)?closed|position\s+has\s+been\s+filled", re.I)
NOT_FOUND_TEXT = re.compile(r"page\s+not\s+found|404\s+error|this\s+page\s+(doesn'?t|does\s+not)\s+exist|job\s+not\s+found", re.I)
LOGIN_TEXT = re.compile(r"sign\s+in\s+to\s+apply|log\s*in\s+to\s+apply|create\s+(an\s+)?account|register\s+to\s+apply|"
                        r"sign\s+up\s+to\s+apply|already\s+have\s+an\s+account", re.I)
SUBMIT_TEXT = re.compile(r"^\s*(submit|send|apply|complete|confirm|finish)(\s+(my\s+)?(application|now|and\s+apply))?\s*$|"
                         r"submit\s+application|send\s+application|apply\s+now", re.I)
NEXT_TEXT = re.compile(r"^\s*(next|continue|next\s+step|save\s+and\s+continue|proceed)\s*$", re.I)
APPLY_BUTTON = re.compile(r"^\s*(apply(\s+now)?|easy\s+apply|quick\s+apply|apply\s+for\s+this\s+job)\s*$", re.I)
ALREADY_APPLIED = re.compile(r"you\s+(have\s+)?applied(\s+for\s+this\s+job)?\s+on|you('ve|\s+have)\s+already\s+applied|application\s+sent\s+on", re.I)
REED_MODAL = "[data-qa=apply-job-modal]"
REED_CV_NAME = f"{REED_MODAL} [class*=cvName_desktop]"
REED_PROFILE = "https://www.reed.co.uk/candidate-profile"
CV_UPLOAD_URL = re.compile(r"reed\.co\.uk/.*cv", re.I)
GONE = re.compile(r"^(listing closed|page not found|page failed to load|already applied)")

# Shared with the dry run network guard: Reed POSTs are let through only while a CV upload is in progress
_guard = {"cv_upload": False, "profile_changed": False, "blocked": []}
DONE_TEXT = re.compile(r"application\s+(has\s+been\s+)?(sent|submitted|received)|thank\s+you\s+for\s+(your\s+)?appl|successfully\s+applied|"
                       r"you('ve|\s+have)\s+applied", re.I)

# Label patterns -> answers.yaml paths. First match wins, so specific patterns come first.
RULES = [
    (r"\bcover(ing)?\s+letter|message\s+to\s+(the\s+)?(hiring|recruiter|employer)|why\s+(are\s+you|do\s+you)\s+.*(interested|apply)", "COVER"),
    (r"\b(cv|resume|résumé|curriculum)\b", "CV"),
    (r"first\s+name|forename|given\s+name", "FIRST"),
    (r"last\s+name|surname|family\s+name", "LAST"),
    (r"^\s*(full\s+)?name\s*\*?$|your\s+name|full\s+name", "personal.full_name"),
    (r"e-?mail", "personal.email"),
    (r"phone|mobile|telephone|contact\s+number", "personal.phone"),
    (r"linked\s*in", "personal.linkedin_url"),
    (r"github|portfolio", "personal.github_url"),
    (r"sponsor", "work_rights.requires_sponsorship"),
    (r"right\s+to\s+work|eligib\w*\s+to\s+work|(legally\s+)?(authori[sz]ed|entitled|permitted)\s+to\s+work|work\s+permit", "work_rights.right_to_work_uk"),
    (r"visa\s+(type|status)|immigration\s+status|current\s+visa", "work_rights.current_visa"),
    (r"(willing|prepared|happy|able)\s+to\s+(undergo|obtain|go\s+through|apply\s+for).{0,40}(clearance|vetting)|eligib\w*\s+(for|to\s+obtain)\s+(sc|security)", "work_rights.willing_to_undergo_sc"),
    (r"security\s+clearance|\bsc\b\s+clear|clearance\s+held", "work_rights.sc_clearance_held"),
    (r"notice\s+period|notice", "job.notice_period"),
    (r"current\s+(salary|package|compensation)", "job.current_salary"),
    (r"salary|compensation|pay\s+expectation|desired\s+pay|expected\s+package", "job.salary_expectation"),
    (r"relocat", "job.willing_to_relocate"),
    (r"remote|hybrid|on\s*-?site|office\s+based|work(ing)?\s+pattern|work(ing)?\s+arrangement", "job.work_pattern"),
    (r"start\s+date|when\s+can\s+you\s+start|available\s+to\s+start|availability", "job.earliest_start_date"),
    (r"years?.*\bpyspark\b|\bpyspark\b.*years?|years?.*\bspark\b", "experience.years_pyspark"),
    (r"years?.*\bpython\b|\bpython\b.*years?", "experience.years_python"),
    (r"years?.*\bsql\b|\bsql\b.*years?", "experience.years_sql"),
    (r"years?.*\baws\b|\baws\b.*years?", "experience.years_aws"),
    (r"years?.*\bazure\b|\bazure\b.*years?", "experience.years_azure"),
    (r"years?\s+(of\s+)?(relevant\s+|total\s+|professional\s+)?experience|how\s+many\s+years", "experience.years_total"),
    (r"degree|highest\s+(level\s+of\s+)?(education|qualification)", "experience.highest_degree"),
    (r"certif", "experience.certifications"),
    (r"gender|\bsex\b", "equal_opportunities.gender"),
    (r"ethnic", "equal_opportunities.ethnicity"),
    (r"disab", "equal_opportunities.disability"),
    (r"\bage\b|date\s+of\s+birth", "equal_opportunities.age_range"),
    (r"location|city|town|where\s+are\s+you\s+based|where\s+do\s+you\s+live", "personal.location"),
]
RULES = [(re.compile(p, re.I), key) for p, key in RULES]

FIELDS_JS = r"""
(scope) => {
  const root = scope || document;
  const els = [...root.querySelectorAll('input, select, textarea')];
  const seen = new Set();
  let n = 0;
  const textOf = el => (el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : '');
  return els.map(el => {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) return null;
    if (el.closest('header, nav, [role=search], [role=banner]')) return null;   // site search bars, not the application
    const style = window.getComputedStyle(el);
    const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && style.visibility !== 'hidden';
    if (!visible && type !== 'file') return null;
    if (el.disabled || el.readOnly) return null;
    const id = 'f' + (n++);
    el.setAttribute('data-agent-id', id);
    let label = el.getAttribute('aria-label') || '';
    const lb = el.getAttribute('aria-labelledby');
    if (!label && lb) label = lb.split(' ').map(i => textOf(document.getElementById(i))).join(' ');
    if (!label && el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); label = textOf(l); }
    if (!label) label = textOf(el.closest('label'));
    let group = '';
    const fs = el.closest('fieldset');
    if (fs) group = textOf(fs.querySelector('legend'));
    if (!group && (type === 'radio' || type === 'checkbox')) {
      const box = el.closest('[role="radiogroup"], [role="group"], .form-group, .question, div');
      if (box && box.parentElement) group = textOf(box.parentElement.querySelector('label, legend, h3, h4, p, span'));
    }
    if (!label) label = el.getAttribute('placeholder') || el.getAttribute('name') || '';
    const required = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label) || /\*\s*$/.test(group);
    const options = el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) : [];
    const name = el.getAttribute('name') || id;
    if ((type === 'radio') && seen.has(name)) return {id, type, name, label, group, required, options, value: el.value, dup: true};
    seen.add(name);
    return {id, type, tag: el.tagName.toLowerCase(), name, label, group, required, options,
            value: type === 'checkbox' || type === 'radio' ? (el.checked ? 'checked' : '') : (el.value || ''),
            accept: el.getAttribute('accept') || ''};
  }).filter(Boolean);
}
"""


def _pause(lo: float = 0.5, hi: float = 2.0) -> None:
    time.sleep(random.uniform(lo, hi))


def load_answers(strict: bool = True) -> dict:
    """strict: stop with a message if answers.yaml is still the empty template (needed before applying)."""
    with open(ANSWERS_PATH) as f:
        answers = yaml.safe_load(f) or {}
    filled = [v for section in answers.values() if isinstance(section, dict) for v in section.values() if v not in (None, "", [])]
    if not filled and not strict:
        print("  config/answers.yaml is empty, so every form question counts as unanswered.")
    elif not filled:
        raise SystemExit("answers.yaml is empty. Fill in config/answers.yaml (your contact details, right to work, notice "
                         "period...) before applying: the agent answers form questions only from it.")
    return answers


def _answer(answers: dict, key: str) -> str | None:
    node = answers
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if node is None or "TODO" in str(node):
        return None
    if isinstance(node, bool):           # YAML reads a bare Yes / No as true / false
        return "Yes" if node else "No"
    return str(node).strip()


def _yes_no(value: str) -> str | None:
    m = re.match(r"\s*(yes|no)\b", value, re.I)
    if m:
        return m.group(1).lower()
    if value.lower() == "any":           # work_pattern "Any": happy with any arrangement
        return "yes"
    return None


def _pick_option(options: list[str], value: str) -> str | None:
    """Best select option or radio label for an answer, or None if nothing fits."""
    clean = [o for o in options if o and not re.match(r"^\s*(please\s+)?(select|choose)|^-+$", o, re.I)]
    v = value.lower().strip()
    for o in clean:
        if o.lower().strip() == v:
            return o
    yn = _yes_no(value)
    if yn:
        for o in clean:
            if re.match(rf"\s*{yn}\b", o, re.I):
                return o
    if re.fullmatch(r"\d+", v):               # years: "6" fits "5+ years" or "5-7 years"
        n = int(v)
        for o in clean:
            nums = [int(x) for x in re.findall(r"\d+", o)]
            if len(nums) >= 2 and nums[0] <= n <= nums[1]:
                return o
        for o in clean:
            nums = [int(x) for x in re.findall(r"\d+", o)]
            if len(nums) == 1 and "+" in o and n >= nums[0]:
                return o
    for o in clean:
        if v in o.lower() or (len(o) > 3 and o.lower() in v):
            return o
    return None


def _key_for(field: dict) -> str | None:
    text = f"{field['group']} {field['label']}".strip()
    for rx, key in RULES:
        if rx.search(text):
            return key
    return None


def _visible_challenge(frame) -> bool:
    """A CAPTCHA frame the person would actually see. Invisible reCAPTCHA (size=invisible) and its small
    "protected by reCAPTCHA" badge load on many normal pages and are not a challenge."""
    url = frame.url or ""
    if not BOT_FRAMES.search(url) or frame.parent_frame is None:
        return False
    if "recaptcha" in url and "anchor" in url and "size=invisible" in url:
        return False
    try:
        el = frame.frame_element()
        box = el.bounding_box() if el.is_visible() else None
    except PlaywrightError:
        return False
    if not box or box["width"] < 60 or box["height"] < 60:
        return False
    return not ("recaptcha" in url and box["height"] <= 70 and box["width"] <= 260)     # the badge


def _check_page(page: Page, response=None) -> None:
    if response is not None and response.status >= 400:
        raise Skip(f"page failed to load (HTTP {response.status})")
    for frame in page.frames:
        if _visible_challenge(frame):
            raise Skip(f"bot check (CAPTCHA) on the page; not attempted (frame: {frame.url[:80]})")
    try:
        body = page.inner_text("body", timeout=10000)
    except PlaywrightError:
        raise Skip("page failed to load")
    if BOT_TEXT.search(body):
        raise Skip(f"bot check on the page (\"{BOT_TEXT.search(body).group(0)}\"); not attempted")
    if NOT_FOUND_TEXT.search(page.title() or "") or NOT_FOUND_TEXT.search(body[:3000]):
        raise Skip("page not found")
    if CLOSED_TEXT.search(body):
        raise Skip(f"listing closed (\"{CLOSED_TEXT.search(body).group(0)}\")")


def _check_login(page: Page, scope) -> None:
    if page.locator("input[type=password]:visible").count():
        raise Skip("login or account creation needed")
    # Only the form or dialog counts: site headers often say "create an account" even when signed in
    forms = scope.locator("form") if scope.evaluate("e => e.tagName") == "BODY" else scope
    text = " ".join(forms.nth(i).inner_text() for i in range(min(forms.count(), 5)))
    if m := LOGIN_TEXT.search(text):
        raise Skip(f"login or account creation needed (\"{m.group(0)}\")")


def _scope(page: Page):
    dialog = page.locator("[role=dialog]:visible, [aria-modal=true]:visible")
    return dialog.last if dialog.count() else page.locator("body")


def _buttons(scope) -> list[tuple[str, object]]:
    out = []
    loc = scope.locator("button:visible, input[type=submit]:visible, a[role=button]:visible")
    for i in range(loc.count()):
        b = loc.nth(i)
        try:
            text = (b.inner_text(timeout=1000) or b.get_attribute("value") or "").strip()
        except PlaywrightError:
            continue
        out.append((text, b))
    return out


def fill_form(page: Page, scope, answers: dict, cv_pdf: Path, cl_pdf: Path, letter_text: str, notes: list[str]) -> None:
    """Fill every field in scope from answers.yaml. Raises Skip on any required question it cannot answer."""
    handle = scope.element_handle()
    fields = page.evaluate(FIELDS_JS, handle)
    radios: dict[str, list[dict]] = {}
    for f in fields:
        if f["type"] == "radio":
            radios.setdefault(f["name"], []).append(f)
    full = _answer(answers, "personal.full_name") or ""

    for f in fields:
        if f.get("dup"):
            continue
        label = (f"{f['group']} / {f['label']}" if f["group"] and f["group"] != f["label"] else f["label"]).strip(" /")
        key = _key_for(f)
        el = page.locator(f'[data-agent-id="{f["id"]}"]')

        if f["type"] == "file":
            if key == "COVER" and cl_pdf is None:
                if f["required"]:
                    raise Skip(f"the form needs a cover letter (\"{label}\") and cover letters are off")
                notes.append(f"left optional cover letter upload empty (cover letters are off): {label}")
                continue
            if key == "COVER":
                el.set_input_files(str(cl_pdf))
            elif key == "CV" or (key is None and re.search(r"pdf|doc", f["accept"], re.I) and f["required"]):
                el.set_input_files(str(cv_pdf))
            elif f["required"]:
                raise Skip(f"question not covered in answers.yaml: file upload \"{label}\"")
            else:
                notes.append(f"left optional upload empty: {label}")
                continue
            notes.append(f"uploaded {'cover letter' if key == 'COVER' else 'CV'}: {label}")
            _pause()
            continue

        if key == "COVER":
            value = letter_text or None
            if value is None and f["required"]:
                raise Skip(f"the form needs a cover letter (\"{label}\") and cover letters are off")
        elif key == "CV":
            value = None
        elif key == "FIRST":
            value = full.split()[0] if full else None
        elif key == "LAST":
            value = " ".join(full.split()[1:]) if full else None
        else:
            value = _answer(answers, key) if key else None

        if value is None:
            if f["value"] and f["type"] not in ("checkbox", "radio"):
                notes.append(f"kept the value the site filled in from your saved profile: {label}")
                continue
            if f["required"]:
                raise Skip(f"question not covered in answers.yaml: \"{label}\"")
            notes.append(f"left optional field empty: {label}")
            continue

        if f["type"] == "radio":
            group = radios.get(f["name"], [f])
            choice = _pick_option([g["label"] for g in group], value)
            if not choice:
                raise Skip(f"answers.yaml has no answer that fits \"{label}\" (options: {[g['label'] for g in group]})")
            target = next(g for g in group if g["label"] == choice)
            page.locator(f'[data-agent-id="{target["id"]}"]').check(force=True)
        elif f["type"] == "checkbox":
            yn = _yes_no(value)
            if yn is None:
                raise Skip(f"answers.yaml has no yes or no answer for checkbox \"{label}\"")
            el.set_checked(yn == "yes", force=True)
        elif f["tag"] == "select":
            choice = _pick_option(f["options"], value)
            if not choice:
                raise Skip(f"answers.yaml has no answer that fits \"{label}\" (options: {f['options'][:8]})")
            el.select_option(label=choice)
        else:
            el.fill("")
            el.press_sequentially(value, delay=random.randint(15, 45)) if len(value) < 120 else el.fill(value)
        notes.append(f"answered \"{label}\" from {'the cover letter' if key == 'COVER' else 'answers.yaml ' + key}")
        _pause()


def _same_file(shown: str, expected: str) -> bool:
    """Reed shortens long names (e.g. "Your_Name_Master_CV_Data_Engi.docx"), so a shortened stem counts."""
    a, b = Path(shown.strip()), Path(expected)
    if not shown.strip() or a.suffix.lower() != b.suffix.lower():
        return False
    return a.stem == b.stem or (len(a.stem) >= min(len(b.stem), 25) and b.stem.startswith(a.stem))


def _reed_cv_shown(page: Page) -> str:
    loc = page.locator(REED_CV_NAME)
    return loc.last.inner_text().strip() if loc.count() else ""


def _upload_to_reed(page: Page, update_button, upload: Path, confirm: bool = False) -> str:
    """Click Reed's Update button, choose the file and wait until Reed accepts it. Returns the upload URL."""
    _guard["cv_upload"] = True
    try:
        update_button.click()
        _pause()
        if confirm:     # the profile page asks "Updating your CV will overwrite... Continue?"
            cont = page.locator("[role=dialog]:visible, .modal:visible").locator("button:has-text('Continue')")
            if cont.count():
                cont.first.click()
                _pause()
        file_input = page.locator("input[type=file]").last
        file_input.wait_for(state="attached", timeout=15000)
        _guard["profile_changed"] = True
        with page.expect_response(lambda r: r.request.method == "POST" and CV_UPLOAD_URL.search(r.url), timeout=60000) as resp:
            file_input.set_input_files(str(upload))
        if not resp.value.ok:
            raise Skip(f"Reed CV upload failed (HTTP {resp.value.status})")
        _pause(2, 3)
        return resp.value.url
    except PlaywrightTimeout:
        raise Skip("Reed did not confirm the CV upload")
    finally:
        _guard["cv_upload"] = False


def _reed_attach_cv(page: Page, modal, upload: Path, dry_run: bool, notes: list[str]) -> str:
    """Replace the CV in Reed's application box with the tailored one and check the name it shows."""
    before = _reed_cv_shown(page)
    if not modal.locator("[data-qa=UpdateCvBtn]").count():
        raise Skip(f"no Update button for the CV in Reed's form; the form shows {before or 'no CV'}")
    url = _upload_to_reed(page, modal.locator("[data-qa=UpdateCvBtn]").first, upload)
    shown = ""
    for _ in range(20):
        shown = _reed_cv_shown(page)
        if _same_file(shown, upload.name):
            break
        page.wait_for_timeout(1000)
    notes.append(f"replaced the CV in Reed's form ({before or 'none'}) with {upload.name} (upload {url.split('?')[0]})"
                 + ("; dry run: the upload was allowed so the name could be checked, nothing was submitted" if dry_run else ""))
    if not _same_file(shown, upload.name):
        raise Skip(f"wrong CV attached (the form shows {shown or 'no CV'}, expected {upload.name})")
    notes.append(f"CV name shown in the form: {shown}")
    if not modal.locator("textarea").count() and not re.search(r"cover(ing)?\s+letter", modal.inner_text(), re.I):
        notes.append("Reed's form for this job has no cover letter field")
    return shown


def restore_profile_cv(ctx, cfg: dict, out_dir: Path) -> str:
    """Put documents.restore_profile_cv_after_run back on the Reed profile."""
    docs = cfg.get("documents") or {}
    src = ROOT / docs["restore_profile_cv_after_run"]
    who = re.sub(r"\W+", "_", candidate_name(cfg)).strip("_")
    name = Path((docs.get("cv_upload_name") or "{name}_CV_{role}.pdf").format(name=who, role=src.stem)).with_suffix(src.suffix).name
    upload = out_dir / "uploads" / "profile" / name
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, upload)
    page = ctx.new_page()
    try:
        _load(page, REED_PROFILE)
        button = page.locator("button[data-element=cv_update]")
        if not button.count():
            return f"could not restore the Reed profile CV: no CV Update button on {REED_PROFILE}"
        _upload_to_reed(page, button.first, upload, confirm=True)
        for i in range(20):
            if Path(name).stem[:25] in page.inner_text("body"):
                return f"restored the Reed profile CV: {name} (from {docs['restore_profile_cv_after_run']})"
            page.wait_for_timeout(1000)
            if i == 10:
                _load(page, REED_PROFILE)
        return f"uploaded {name} to the Reed profile, but the profile page does not show it yet; please check {REED_PROFILE}"
    except Skip as e:
        return f"could not restore the Reed profile CV ({e}); please upload {src.name} at {REED_PROFILE}"
    except PlaywrightError as e:
        return f"could not restore the Reed profile CV (browser error: {str(e).splitlines()[0][:120]}); please upload {src.name} at {REED_PROFILE}"
    finally:
        page.close()


def _load(page: Page, url: str) -> None:
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        response = None
    except PlaywrightError as e:
        raise Skip(f"page failed to load ({e.__class__.__name__})")
    _pause(1, 2.5)
    _check_page(page, response)


def _open_application(page: Page, job: Job) -> object:
    """Go to the job and open its application form. Returns the scope that holds the form."""
    _load(page, _job_url(job))

    on_reed = "reed.co.uk" in page.url
    if on_reed and page.locator("a:visible:has-text('Sign in'), a:visible:has-text('Sign In')").count():
        raise Skip("login needed: the Reed session in browser_profile/ is not signed in")
    if on_reed and (m := ALREADY_APPLIED.search(page.inner_text("body"))):
        raise Skip(f"already applied on Reed (\"{m.group(0)}\"); record it with --mark-applied {job.job_id}")

    if on_reed:
        # Reed jobs are only ever applied to through Reed's own application box. Inputs elsewhere on the
        # page (the job search bar, alerts) are never touched.
        btn = page.locator("[data-qa=apply-btn]:visible")
        if not btn.count():
            raise Skip("no Reed Apply button on the page")
        btn.first.click()
        try:
            page.locator(REED_MODAL).last.wait_for(state="visible", timeout=15000)
        except PlaywrightTimeout:
            raise Skip("Reed's application box did not open")
        _pause(1.5, 3)
        modal = page.locator(REED_MODAL).last
        if re.search(r"apply\s+on\s+external\s+site|take\s+you\s+to\s+the\s+employer", modal.inner_text(), re.I):
            raise Skip("external application: Reed sends you to the employer's own site, which the agent does not fill in. "
                       "Reed now lists the job under Jobs applied for, but the application is not finished; apply on "
                       "the employer's site by hand, then --mark-applied " + job.job_id)
        _check_login(page, modal)
        return page, modal

    # Elsewhere: if the form is not already on the page, click the job's own Apply button (it may open a new tab)
    if not page.locator("input[type=file], textarea, form input[type=email]").count():
        apply_btn = next((b for t, b in _buttons(page.locator("body")) if APPLY_BUTTON.match(t)), None)
        if apply_btn is None:
            link = page.locator("a:visible").filter(has_text=re.compile(r"^\s*apply(\s+now)?\s*$", re.I))
            apply_btn = link.first if link.count() else None
        if apply_btn is None:
            raise Skip("no Apply button or form found on the page")
        try:
            # Some Apply buttons open a new tab (external sites), others open the form in place
            with page.context.expect_page(timeout=5000) as new_tab:
                apply_btn.click()
            page = new_tab.value
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except PlaywrightTimeout:
            pass
        _pause(1.5, 3)
        _check_page(page)
    scope = _scope(page)
    _check_login(page, scope)
    return page, scope


def _wait_enabled(page: Page, button, seconds: int = 30) -> None:
    """Reed fades Submit while it processes the CV upload; wait until it is clickable."""
    for _ in range(seconds * 2):
        cls = button.get_attribute("class") or ""
        if button.is_enabled() and button.get_attribute("aria-disabled") != "true" and not re.search(r"\bdisabled\b", cls):
            return
        page.wait_for_timeout(500)
    raise Skip(f"Submit button stayed disabled for {seconds}s; not submitted")


def _shoot(page: Page, path) -> None:
    """Compressed JPEG. Only _Submitted and _Unconfirmed screenshots are kept after 7 days (run.py --cleanup)."""
    page.screenshot(path=str(path), full_page=True, type="jpeg", quality=55)


def attempt(ctx, job: Job, tailored: dict, answers: dict, dry_run: bool, out_dir: Path, cfg: dict) -> dict:
    """Try one application. Returns {status, reason, screenshot, notes, cv_shown}."""
    # The tailored PDF, under the name a recruiter should see (documents.cv_upload_name)
    upload = out_dir / "uploads" / job.job_id / tailor.upload_name(job, cfg)
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(tailored["cv"]).with_suffix(".pdf"), upload)
    letters = bool((cfg.get("documents") or {}).get("write_cover_letters", True)) and tailored.get("cover_letter")
    cl_pdf = Path(tailored["cover_letter"]).with_suffix(".pdf") if letters else None
    letter_text = tailored.get("letter_text", "") if letters else ""
    shot = out_dir / Path(tailored["cv"]).name.replace("_CV.docx", "_Screenshot.jpg")
    notes: list[str] = []
    cv_shown = ""
    page = ctx.new_page()
    try:
        page, scope = _open_application(page, job)
        on_reed_form = bool(page.locator(REED_MODAL).count())
        if on_reed_form:
            scope = page.locator(REED_MODAL).last
            cv_shown = _reed_attach_cv(page, scope, upload, dry_run, notes)
        for step in range(1, 7):
            fill_form(page, scope, answers, upload, cl_pdf, letter_text, notes)
            _check_page(page)
            buttons = _buttons(scope)
            submit = next((b for t, b in buttons if SUBMIT_TEXT.search(t)), None)
            nxt = next((b for t, b in buttons if NEXT_TEXT.match(t)), None)
            if submit is not None or nxt is None:
                break
            if dry_run:
                notes.append(f"multi step form: dry run filled step {step} only and did not click Next")
                break
            nxt.click()
            _pause(1.5, 3)
            _check_page(page)
            scope = _scope(page)
            _check_login(page, scope)
        _shoot(page, shot)
        if submit is None and not (dry_run and nxt is not None):
            raise Skip("filled the form but found no submit button")
        if on_reed_form:
            # Last check before submitting: the CV in the form must be the tailored file
            shown = _reed_cv_shown(page)
            if shown and not _same_file(shown, upload.name):
                raise Skip(f"wrong CV attached (the form shows {shown}, expected {upload.name})")
        if dry_run:
            return {"status": "dry_run", "reason": "dry run: form filled, not submitted", "screenshot": str(shot),
                    "notes": notes, "cv_shown": cv_shown}
        _wait_enabled(page, submit)
        submit.click()
        # Only a success message from the site counts as applied
        for _ in range(20):
            page.wait_for_timeout(1000)
            if m := DONE_TEXT.search(page.inner_text("body")):
                done_shot = str(shot).replace("_Screenshot.jpg", "_Submitted.jpg")
                _shoot(page, done_shot)
                notes.append(f"success message seen: \"{m.group(0)}\"")
                return {"status": "applied", "reason": "submitted", "screenshot": done_shot, "notes": notes, "cv_shown": cv_shown}
        after_shot = str(shot).replace("_Screenshot.jpg", "_Unconfirmed.jpg")
        _shoot(page, after_shot)
        return {"status": "unconfirmed", "reason": "submit not confirmed: clicked Submit but no success message appeared; "
                f"check Reed's Jobs applied for, then --mark-applied {job.job_id} if it went through",
                "screenshot": after_shot, "notes": notes, "cv_shown": cv_shown}
    except Skip as e:
        try:
            _shoot(page, shot)
        except PlaywrightError:
            pass
        return {"status": "skipped", "reason": str(e), "screenshot": str(shot) if shot.exists() else "", "notes": notes,
                "cv_shown": cv_shown}
    except PlaywrightError as e:
        return {"status": "skipped", "reason": f"browser error: {str(e).splitlines()[0][:150]}", "screenshot": "", "notes": notes,
                "cv_shown": cv_shown}
    finally:
        shutil.rmtree(upload.parent, ignore_errors=True)      # a renamed copy of the kept PDF
        for p in ctx.pages[1:]:
            try:
                p.close()
            except PlaywrightError:
                pass


# ---------- limits and spacing ----------

def _in_hours(window: str, now: dt.datetime) -> bool:
    start, end = (dt.time.fromisoformat(x.strip()) for x in window.split("-"))
    return start <= now.time() <= end


def _dry_run_guard(route, req) -> None:
    """In dry run the browser cannot send anything: every POST, PUT, PATCH and DELETE is aborted, so no form,
    click or upload can submit or change an account. The one exception is a Reed request while a CV upload
    is in progress, so the name in the form can be checked; the profile CV is restored after the run."""
    if req.method in ("GET", "HEAD", "OPTIONS") or (_guard["cv_upload"] and "reed.co.uk" in req.url.split("/")[2]):
        route.continue_()
    else:
        if "reed.co.uk" in req.url.split("/")[2]:
            _guard["blocked"].append(f"{req.method} {req.url.split('?')[0]}")
        route.abort()


def _job_url(job: Job) -> str:
    return job.apply_url if job.apply_url and "reed.co.uk" not in job.apply_url else job.link


def precheck(jobs: list[Job], dry_run: bool) -> dict[str, str]:
    """llm.check_apply_page_first: open each job page before tailoring. Returns {job key: reason} for jobs whose
    listing is closed, gone or already applied to. Blocked or login pages still get documents."""
    out = {}
    with sync_playwright() as p:
        ctx = browser.launch(p)
        ctx.route("**/*", _dry_run_guard)       # looking only, in both modes
        _guard["blocked"].clear()
        for n, job in enumerate(jobs, 1):
            if progress.stop_requested():
                break
            progress.say(f"Checking job pages are still open, {n} of {len(jobs)}")
            page = ctx.new_page()
            try:
                _load(page, _job_url(job))
                if "reed.co.uk" in page.url and (m := ALREADY_APPLIED.search(page.inner_text("body"))):
                    raise Skip(f"already applied on Reed (\"{m.group(0)}\"); record it with --mark-applied {job.job_id}")
            except Skip as e:
                if GONE.match(str(e)):
                    out[job.key] = str(e)
            except PlaywrightError:
                pass
            finally:
                page.close()
        ctx.close()
    return out


def apply_all(queue: list[tuple[Job, dict]], cfg: dict, out_dir: Path) -> tuple[dict[str, dict], str]:
    """Apply to the queued jobs in order, within every limit in config.yaml.
    Returns (results by job key, what happened to the Reed profile CV)."""
    run = cfg["run"]
    dry_run = bool(run.get("dry_run", True))
    answers = load_answers()
    history = ledger.live_applications()
    now = dt.datetime.now()
    today_count = sum(1 for when, _ in history if when.date() == now.date())
    week = [key.lower() for when, key in history if when >= now - dt.timedelta(days=7)]
    stamps = [when for when, _ in history if when >= now - dt.timedelta(hours=1)]
    results: dict[str, dict] = {}
    done_this_run = 0

    with sync_playwright() as p:
        ctx = None
        for n, (job, tailored) in enumerate(queue, 1):
            limit_key = job.limit_key.lower()
            if progress.stop_requested():         # never between Submit and the result: only before an application starts
                results[job.key] = {"status": "deferred", "reason": progress.STOPPED}
                continue
            if today_count + done_this_run >= run["daily_cap"]:
                results[job.key] = {"status": "deferred", "reason": f"daily cap of {run['daily_cap']} reached; rolls to the next run"}
                continue
            if week.count(limit_key) >= run["max_per_agency_per_week"]:
                results[job.key] = {"status": "deferred",
                                    "reason": f"{job.limit_key} already has {run['max_per_agency_per_week']} applications this week"}
                continue
            if not dry_run:
                if not _in_hours(run["active_hours"], dt.datetime.now()):
                    results[job.key] = {"status": "deferred", "reason": f"outside active hours {run['active_hours']}; rolls to the next run"}
                    continue
                stamps = [s for s in stamps if s >= dt.datetime.now() - dt.timedelta(hours=1)]
                if len(stamps) >= run["max_per_hour"]:
                    wait = (stamps[0] + dt.timedelta(hours=1) - dt.datetime.now()).total_seconds()
                    print(f"  max_per_hour reached, waiting {wait / 60:.0f} minutes", flush=True)
                    progress.say(f"Waiting {max(1, round(wait / 60))} minutes (hourly limit reached)")
                    if not progress.sleep(max(0, wait)):
                        results[job.key] = {"status": "deferred", "reason": progress.STOPPED}
                        continue
                if done_this_run:
                    gap = random.uniform(run["min_gap_minutes"], run["max_gap_minutes"]) * 60
                    print(f"  waiting {gap / 60:.1f} minutes before the next application", flush=True)
                    progress.say(f"Waiting {max(1, round(gap / 60))} minutes before the next application")
                    if not progress.sleep(gap):
                        results[job.key] = {"status": "deferred", "reason": progress.STOPPED}
                        continue
                    if not _in_hours(run["active_hours"], dt.datetime.now()):
                        results[job.key] = {"status": "deferred", "reason": f"outside active hours {run['active_hours']}; rolls to the next run"}
                        continue
            if ctx is None:
                ctx = browser.launch(p)
                if dry_run:
                    ctx.route("**/*", _dry_run_guard)
            print(f"  {'filling' if dry_run else 'applying'}: {job.title} | {job.employer or job.company}", flush=True)
            progress.say(f"{'Filling in (practice)' if dry_run else 'Applying to'} {job.employer or job.company}, {n} of {len(queue)}")
            res = attempt(ctx, job, tailored, answers, dry_run, out_dir, cfg)
            results[job.key] = res
            print(f"    -> {res['status']}: {res['reason']}", flush=True)
            if res["status"] in ("applied", "dry_run"):
                done_this_run += 1
                week.append(limit_key)
                stamps.append(dt.datetime.now())
        profile = ""
        if ctx is not None:
            if _guard["profile_changed"] and (cfg.get("documents") or {}).get("restore_profile_cv_after_run"):
                progress.say("Putting your Reed profile CV back")
                profile = restore_profile_cv(ctx, cfg, out_dir)
                print(f"  {profile}", flush=True)
            if dry_run and _guard["blocked"]:
                print(f"  dry run guard blocked {len(_guard['blocked'])} Reed requests: {sorted(set(_guard['blocked']))[:5]}", flush=True)
            ctx.close()
    return results, profile
