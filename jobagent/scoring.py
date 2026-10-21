"""Score a job 0 to 100 against profile.md and list minimum qualifications met and missed.

Rule based so the result is explainable. Term lists below come from the Skills and Experience
sections of profile.md and projects/. HAVE: things the profile states. MISSING: common asks the
profile does not cover.

CHANGE THESE: the HAVE and MISSING lists below are set up for an AWS data and AI engineer. Edit them to match
your own skills (HAVE) and the common asks you don't have (MISSING). They only drive the quick rule score that
decides which jobs go to Claude; Claude then scores against profile.md itself.
"""
import re

from .models import Job

HAVE = {
    "Python": r"python",
    "PySpark": r"pyspark",
    "Spark": r"(apache\s+)?spark(\s+sql)?",
    "SQL": r"sql|t-sql",
    "AWS": r"aws|amazon\s+web\s+services",
    "S3": r"s3",
    "Glue": r"(aws\s+)?glue",
    "Lambda": r"lambda",
    "Step Functions": r"step\s+functions",
    "Athena": r"athena",
    "Redshift": r"redshift",
    "EMR": r"emr",
    "SageMaker": r"sagemaker",
    "Bedrock": r"bedrock",
    "Lake Formation": r"lake\s+formation",
    "CloudWatch": r"cloudwatch",
    "Iceberg": r"iceberg",
    "Lakehouse": r"lakehouse|medallion",
    "Data lake": r"data\s+lakes?",
    "CDC": r"cdc|change\s+data\s+capture",
    "ETL/ELT": r"etl|elt|data\s+pipelines?|pipelines?",
    "Orchestration (Airflow)": r"airflow|orchestration",
    "Terraform": r"terraform",
    "CloudFormation": r"cloudformation",
    "Infrastructure as code": r"infrastructure\s+as\s+code|iac",
    "CI/CD": r"ci\s*/\s*cd|continuous\s+(integration|delivery|deployment)|codepipeline|devops",
    "Azure DevOps": r"azure\s+devops",
    "Git": r"git|github|version\s+control",
    "Power BI": r"power\s*bi",
    "Dashboards": r"dashboards?|reporting|data\s+visuali[sz]ation",
    "Data modelling": r"data\s+model(l)?ing|dimensional\s+model(l)?ing|kimball|star\s+schema|data\s+warehous\w*",
    "Data quality": r"data\s+quality|data\s+validation",
    "Data governance": r"data\s+governance|lineage|data\s+catalog\w*|metadata",
    "GDPR / privacy": r"gdpr|data\s+protection|privacy|pii",
    "Security (IAM, KMS)": r"iam|kms|rbac|access\s+control|encryption",
    "Pandas": r"pandas",
    "Parquet": r"parquet",
    "Performance tuning": r"performance\s+(tuning|optimi[sz]ation)|query\s+optimi[sz]ation",
    "Big data": r"big\s+data|large[\s-]+scale\s+data|distributed\s+(data|computing|processing)",
    "APIs": r"rest(ful)?\s*apis?|apis?|jdbc|soap",
    "Machine learning": r"machine\s+learning|ml\s+models?|predictive\s+model\w*|supervised",
    "XGBoost / LightGBM": r"xgboost|lightgbm|gradient\s+boosting",
    "Deep learning (LSTM)": r"lstm|neural\s+networks?|deep\s+learning",
    "Forecasting": r"forecast\w*|time[\s-]+series",
    "Statistics": r"statistic\w*|hypothesis\s+test\w*|a/b\s+test\w*",
    "EDA": r"exploratory\s+data\s+analysis|eda",
    "Geospatial": r"geospatial|network\s+analysis",
    "LLMs": r"llms?|large\s+language\s+models?|foundation\s+models?",
    "Generative AI": r"gen\s*ai|generative\s+ai",
    "AI agents": r"ai\s+agents?|agentic|agents?|tool\s+(use|calling)|function\s+calling",
    "Prompt engineering": r"prompt\s+engineering|prompting|prompts?",
    "LLM evaluation": r"evals?|evaluation\s+(framework|suite)s?|llm\s+evaluation|model\s+evaluation",
    "Guardrails / responsible AI": r"guardrails?|responsible\s+ai|ai\s+safety|ai\s+governance",
    "Claude": r"claude|anthropic",
    "Copilot": r"copilot",
    "Ollama": r"ollama",
    "Stakeholder management": r"stakeholders?|business\s+users",
    "Client delivery / consulting": r"client[\s-]+facing|consult\w*|customer[\s-]+facing|clients?",
    "Requirements and specs": r"requirements?\s+gathering|discovery|technical\s+specifications?|solution\s+design|proofs?\s+of\s+concept|poc",
    "Mentoring / knowledge transfer": r"mentor\w*|coach\w*|knowledge\s+(transfer|sharing)|guid(e|ing)\s+(junior|other)",
    "Agile": r"agile|scrum",
    "Communication": r"communicat\w*|presentations?|presenting|non[\s-]+technical|explain\w*",
    "Life sciences": r"life\s+sciences?|pharma\w*|iqvia|hcp|commercial\s+analytics",
    "Retail": r"retail",
    "Public sector / higher education": r"public\s+sector|higher\s+education|universit\w*",
    "Computer science degree": r"(degree|bsc|msc|masters?|ba|ma)\b[^.]{0,60}(computer\s+science|computing|stem|engineering|mathematics|quantitative|technical|related)|computer\s+science\s+degree",
    "Cloud": r"cloud(\s+platforms?|\s+services|[\s-]+native)?",
}

MISSING = {
    "Azure": r"(microsoft\s+)?azure(?!\s+devops)",
    "Azure Data Factory": r"azure\s+data\s+factory|adf",
    "Synapse": r"synapse",
    "Microsoft Fabric": r"microsoft\s+fabric|ms\s+fabric|fabric",
    "Azure OpenAI": r"azure\s+openai",
    "Databricks": r"databricks|unity\s+catalog|delta\s+lake|delta\s+live\s+tables",
    "Snowflake": r"snowflake",
    "dbt": r"dbt",
    "GCP": r"gcp|google\s+cloud|bigquery|vertex\s+ai|dataflow",
    "Kafka / streaming": r"kafka|kinesis|flink|event\s+hubs|real[\s-]+time\s+streaming|stream\s+processing",
    "Java": r"java(?!script)",
    "Scala": r"scala",
    "C# / .NET": r"c#|\.net|dotnet",
    "C++": r"c\+\+",
    "Go": r"golang",
    "Rust": r"rust",
    "JavaScript / TypeScript": r"javascript|typescript|node\.?js|react",
    "Docker": r"docker|containers?|containeri[sz]\w*",
    "Kubernetes": r"kubernetes|k8s|eks|aks",
    "MLflow / MLOps tooling": r"mlflow|kubeflow|mlops",
    "PyTorch / TensorFlow": r"pytorch|tensorflow|keras|jax",
    "scikit-learn": r"scikit[\s-]+learn|sklearn",
    "LangChain / LlamaIndex": r"langchain|langgraph|llamaindex|llama\s+index|crewai|autogen",
    "OpenAI API": r"openai|gpt[\s-]?\d",
    "RAG / vector search": r"rag|retrieval[\s-]+augmented|vector\s+(databases?|stores?|search)|embeddings?|pinecone|weaviate|pgvector",
    "Fine tuning": r"fine[\s-]*tun\w*|lora|rlhf",
    "Hugging Face": r"hugging\s*face|transformers\s+library",
    "NLP": r"nlp|natural\s+language\s+processing",
    "Computer vision": r"computer\s+vision|image\s+recognition|opencv",
    "Tableau / Looker / Qlik": r"tableau|looker|qlik",
    "Informatica / SSIS / Talend": r"informatica|ssis|talend|matillion|fivetran|alteryx",
    "Hadoop / Hive": r"hadoop|hive|hdfs",
    "NoSQL": r"nosql|mongodb|cassandra|dynamodb|cosmos\s*db",
    "PostgreSQL / MySQL / SQL Server / Oracle": r"postgres\w*|mysql|sql\s+server|oracle",
    "SAS / R": r"sas|\br\b(?=\s*(,|/|and|or)\s*(python|sql))",
    "Salesforce / SAP": r"salesforce|sap",
    "Microservices": r"microservices?",
    "PhD": r"phd|doctorate",
}

_HAVE = {k: re.compile(rf"(?<![\w.])(?:{v})(?![\w])", re.I) for k, v in HAVE.items()}
_MISSING = {k: re.compile(rf"(?<![\w.])(?:{v})(?![\w])", re.I) for k, v in MISSING.items()}

REQ_HEAD = re.compile(
    r"requirement|essential|what you('ll|\s+will)\s+(need|bring|have)|you('ll|\s+will)\s+(need|bring|have)|"
    r"what\s+we('re|\s+are)\s+looking\s+for|looking\s+for|about\s+you|who\s+you\s+are|you\s+have|"
    r"skills|experience\s+(required|needed)|qualifications|must[\s-]+haves?|ideal\s+candidate|person\s+spec|"
    r"what\s+you\s+need|key\s+competenc", re.I)
DESIRABLE_HEAD = re.compile(r"desirable|nice[\s-]+to[\s-]+have|bonus|preferred|advantageous|beneficial|would\s+be\s+a\s+plus|plus\s+points", re.I)
END_HEAD = re.compile(
    r"benefits|what\s+we\s+offer|perks|in\s+return|package|why\s+join|why\s+work|about\s+us|about\s+the\s+(company|team|client)|"
    r"responsibilit|what\s+you('ll|\s+will)\s+(do|be\s+doing)|day[\s-]+to[\s-]+day|the\s+role|role\s+overview|how\s+to\s+apply|"
    r"salary|location|diversity|equal\s+opportunit|our\s+values|interview\s+process|legal|disclaimer|ways\s+of\s+working|"
    r"inclusi|travel|working\s+pattern|hybrid", re.I)
BOILERPLATE = re.compile(
    r"inclusive|equal\s+opportunit|discriminat|protected\s+characteristic|disability|adjustments|accommodat|"
    r"diverse\s+perspectives|all\s+backgrounds|charter|travel\s+(should|is|will)|office\s+move|location\s+notice|"
    r"recruitment\s+process|apply\s+(now|today)|click\s+apply|exciting\s+opportunity|we\s+celebrate|"
    r"benefits|pension|holiday|annual\s+leave|bonus\s+scheme", re.I)
QUAL_HINT = re.compile(
    r"experience|knowledge|proficien|skills?|familiar|understanding|degree|background|ability|able\s+to|expertise|"
    r"strong|hands[\s-]+on|track\s+record|comfortable|fluent|demonstrable|proven|years", re.I)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(?:\d{1,2})?\s*\+?\s*years?", re.I)
ALTERNATIVE = re.compile(r"\bor\b|/|such\s+as|e\.g\.|similar|equivalent|comparable|any\s+(major\s+)?cloud|alike", re.I)
SOFT = re.compile(r"communicat|stakeholder|collaborat|problem[\s-]+solv|team|ownership|curious|initiative|detail|"
                  r"self[\s-]+starter|independent|prioriti|deadline|mentor|consult|client|agile|business", re.I)

_years = {}


def profile_years() -> int:
    """config.yaml candidate.years_experience; 0 switches the years check off."""
    if "n" not in _years:
        from .settings import load_config
        _years["n"] = int((load_config().get("candidate") or {}).get("years_experience") or 0)
    return _years["n"]
ESSENTIAL_CAP = 50      # cv_rules_for_agent.md section 2: any missing essential caps the score at 50
ESSENTIAL_WORDS = re.compile(r"essential|required|requirement|must|you\s+will\s+need|at\s+least\s+one", re.I)
# HAVE entries that are not technical skills. A line that only matches these counts as soft.
SOFT_TERMS = {
    "Stakeholder management", "Client delivery / consulting", "Requirements and specs", "Mentoring / knowledge transfer",
    "Agile", "Communication", "Life sciences", "Retail", "Public sector / higher education", "Computer science degree",
    "Cloud", "Dashboards", "APIs", "ETL/ELT",
}
HARD_DEPTH = 8          # this many distinct technical matches counts as full depth
PROSE_QUAL = re.compile(
    r"successful\s+candidate|ideal\s+candidate|you\s+(will|should|must)\s+have|you('ll|\s+will)\s+(need|bring)|"
    r"you\s+have|we('re|\s+are)\s+looking\s+for|is\s+(also\s+)?(expected|essential|required|key)|must\s+have|required", re.I)

# Title families, and the base CV each maps to (cv_rules_for_agent.md rule 0).
ANALYST = re.compile(r"\b(data|insights?|product|bi|business\s+intelligence|ai|analytics)\s+analyst", re.I)
FAMILIES = [
    (re.compile(r"forward\s+deployed|solutions?\s+engineer|customer\s+engineer|ml\s+platform|ai\s+engineer|genai|gen\s+ai|llm|ai\s+data\s+engineer|"
                r"applied\s+ai|ai\s+developer|ai\s+specialist|prompt\s+engineer|deployment\s+strategist|ai\s+deployment|"
                r"ai\s+solutions?\s+(architect|consultant)", re.I), 20, "cv_ai_engineering.docx"),
    (re.compile(r"data\s+engineer|data\s+platform|analytics\s+engineer|big\s+data|spark|lakehouse|etl\s+developer|data\s+developer", re.I), 20, "cv_data_engineering.docx"),
    (re.compile(r"data\s+scien|machine\s+learning|ml\s+engineer|applied\s+scien|quant\w*\s+(researcher|developer)", re.I), 18, "cv_data_science.docx"),
    # Analyst roles from search.titles only; Business, Claims or Financial Analyst are not targets (cv_rules_for_agent.md section 0)
    (ANALYST, 18, "cv_data_science.docx"),
    (re.compile(r"\b(data|ai|analytics|ml|cloud|life\s+sciences?|genai|technical)\b.{0,30}\bconsultant|consultant\b.{0,30}\b(data|ai|analytics|ml|genai)\b|"
                r"solutions?\s+architect|technical\s+account|client\s+delivery", re.I), 16, "cv_consulting.docx"),
]
# A target title needs a topic word and a role word, e.g. "Data Engineer", "AI Consultant".
TARGET_TOPIC = re.compile(r"\b(data|ai|a\.i\.|ml|machine\s+learning|llm|genai|gen\s+ai|analytics|aws|mlops|lakehouse|spark)\b", re.I)
TARGET_ROLE = re.compile(r"\b(engineer\w*|scientist|developer|architect|consultant|specialist|programmer)\b", re.I)
SENIOR_TITLE = re.compile(r"\b(principal|head\s+of|director|staff|vp|chief)\b", re.I)


def terms(text: str, table: dict) -> set[str]:
    return {name for name, rx in table.items() if rx.search(text)}


def _is_heading(line: str) -> bool:
    bare = line.lstrip("•-*· ").rstrip(":").strip()
    return (len(bare) <= 70 and not line.startswith("•") and len(bare.split()) <= 9
            and (line.rstrip().endswith(":") or not bare.endswith(".")))


def extract_requirements(description: str) -> tuple[list[str], list[str]]:
    """Return (essential lines, desirable lines) from the description."""
    essential, desirable = [], []
    mode = None
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _is_heading(line):
            if DESIRABLE_HEAD.search(line):
                mode = "desirable"
                continue
            if REQ_HEAD.search(line) and not END_HEAD.search(line):
                mode = "essential"
                continue
            if END_HEAD.search(line):
                mode = None
                continue
        if mode is None:
            continue
        # Inline desirable markers inside the essential list
        target = desirable if (mode == "desirable" or DESIRABLE_HEAD.search(line)) else essential
        for part in re.split(r"(?<=[.;])\s+(?=[A-Z])", line.lstrip("•-*· ").strip()):
            if len(part) >= 12 and not BOILERPLATE.search(part):
                target.append(part)

    if not essential:
        # No clear section: fall back to bullets and prose sentences that read like qualifications
        for raw in description.splitlines():
            parts = [raw.lstrip("•-*· ").strip()] if raw.startswith("•") else re.split(r"(?<=[.!?])\s+(?=[A-Z])", raw)
            for line in parts:
                if not (12 <= len(line) <= 400) or BOILERPLATE.search(line) or not QUAL_HINT.search(line):
                    continue
                if raw.startswith("•") or PROSE_QUAL.search(line):
                    (desirable if DESIRABLE_HEAD.search(line) else essential).append(line)
    return essential[:20], desirable[:15]


def classify(line: str) -> tuple[str, str]:
    """Return (verdict, note) for one qualification line: met | missed | unclear."""
    have, missing = terms(line, _HAVE), terms(line, _MISSING)
    years = [int(m.group(1)) for m in YEARS.finditer(line) if 0 < int(m.group(1)) <= 30]
    if years and profile_years() and min(years) > profile_years():
        return "missed", f"asks for {min(years)}+ years, profile has {profile_years()}+"
    if missing and not have:
        return "missed", ", ".join(sorted(missing))
    if missing and have:
        if ALTERNATIVE.search(line):
            return "met", f"has {', '.join(sorted(have))} (alternatives accepted)"
        return "partial", f"has {', '.join(sorted(have))}; lacks {', '.join(sorted(missing))}"
    if have - SOFT_TERMS:
        return "met", ", ".join(sorted(have))
    if have:
        return "soft", ", ".join(sorted(have))
    if SOFT.search(line):
        return "soft", "soft skill shown in profile"
    return "unclear", ""


# Solutions Engineer and Customer Engineer count only when the title or the first paragraph is about AI, ML, data or analytics
CONDITIONAL = re.compile(r"\b(solutions?|customer)\s+engineer", re.I)
DATA_AI = re.compile(r"\b(ai|a\.i\.|artificial\s+intelligence|ml|machine\s+learning|data|analytics|llms?|genai|gen\s+ai|generative)\b", re.I)


def first_paragraph(text: str) -> str:
    return (text or "").strip()[:600]


def needs_intro(title: str) -> bool:
    """True when the title alone can't decide: a Solutions or Customer Engineer title with no AI or data word."""
    return bool(CONDITIONAL.search(title)) and not DATA_AI.search(title)


# Software Engineer and Developer titles count only with a data or AI word in the title, and need a higher score
SOFTWARE = re.compile(r"software\s+engineer|developer", re.I)
SOFTWARE_FOCUS = re.compile(r"\b(data|ai|a\.i\.|ml|machine\s+learning|applied\s+ai|genai|gen\s+ai|llms?)\b", re.I)
SOFTWARE_THRESHOLD = 80
# Lead, Head of and Principal roles: a line management requirement is a missing essential (the profile has none)
LEAD_TITLE = re.compile(r"\b(lead|head\s+of|principal)\b", re.I)
LINE_MGMT = re.compile(r"line[\s-]+manag\w*|people\s+manag\w*|direct\s+reports?|manag(e|es|ing)\s+(a|the|your)?\s*(small\s+|growing\s+)?team|"
                       r"team\s+management|manag(e|ing)\s+(and\s+develop(ing)?\s+)?(a\s+team\s+of|engineers|analysts)", re.I)
NOT_REQUIRED = re.compile(r"\b(no|not|without|if|where|when)\b[^.]{0,30}$", re.I)     # "no line management", Barclays' "If managing a team, ..."


def is_software_role(title: str) -> bool:
    return bool(SOFTWARE.search(title))


def threshold_for(title: str, cfg: dict) -> int:
    base = cfg["run"]["match_threshold"]
    return max(base, cfg["run"].get("software_engineer_threshold", SOFTWARE_THRESHOLD)) if is_software_role(title) else base


def line_management_line(title: str, description: str) -> str:
    """For a Lead, Head of or Principal title: the listing line that asks for line or people management, else ""."""
    if not LEAD_TITLE.search(title or ""):
        return ""
    for raw in (description or "").splitlines():
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", raw.strip()):
            m = LINE_MGMT.search(part)
            if m and not NOT_REQUIRED.search(part[:m.start()]) and not DESIRABLE_HEAD.search(part):
                return part.lstrip("•-*· ").strip()[:300]
    return ""


def check_line_management(job: Job) -> bool:
    """cv_rules_for_agent.md section 2: treat a line management requirement on a Lead role as a missing essential."""
    if job.status not in ("new", "eligible") and not job.reason.startswith("missing essential"):
        return False
    if any("management" in m["requirement"].lower() for m in job.missing_essentials or []):
        return False
    quote = line_management_line(job.title, job.description)
    if not quote:
        return False
    job.missing_essentials = list(job.missing_essentials or []) + [{"requirement": "line management", "quote": quote}]
    job.score = min(job.score, ESSENTIAL_CAP)
    job.status = "skipped"
    job.reason = (job.reason + "; line management") if job.reason.startswith("missing essential") else "missing essential: line management"
    return True


def is_target_title(title: str, intro: str = "") -> bool:
    """intro: the start of the description (or the search snippet), used for Solutions and Customer Engineer titles."""
    if SOFTWARE.search(title) and not SOFTWARE_FOCUS.search(title):
        return False                    # backend, full stack, frontend or Python Developer with no data or AI focus
    if CONDITIONAL.search(title):
        return bool(DATA_AI.search(title) or DATA_AI.search(first_paragraph(intro)))
    return bool((TARGET_TOPIC.search(title) and TARGET_ROLE.search(title))
                or any(rx.search(title) for rx, _, _ in FAMILIES))


def base_cv_for(title: str) -> str:
    for rx, _, cv in FAMILIES:
        if rx.search(title):
            return cv
    return "cv_data_engineering.docx"


def score(job: Job) -> None:
    essential, desirable = extract_requirements(job.description)
    met, missed, unclear, partial, soft = [], [], [], 0, 0
    essential_missing = []
    for line in essential:
        verdict, note = classify(line)
        label = f"{line[:160]}" + (f"  [{note}]" if note else "")
        if verdict == "met":
            met.append(label)
        elif verdict == "soft":
            soft += 1
            met.append(label)
        elif verdict == "partial":
            partial += 1
            met.append(label + " (partial)")
        elif verdict == "missed":
            missed.append(label)
            lacking = terms(line, _MISSING)
            if lacking and ESSENTIAL_WORDS.search(line) and not DESIRABLE_HEAD.search(line):
                essential_missing += sorted(lacking)
        else:
            unclear.append(label)

    text = f"{job.title}\n{job.description}"
    have_all, missing_all = terms(text, _HAVE), terms(text, _MISSING)
    notes = []

    # 1. Minimum qualifications: 55 points
    n = len(essential)
    if n:
        # Hard skill met = 1, soft skill only = 0.5, partial = 0.5, unclear = 0.3, missed = 0
        coverage = (len(met) - partial - soft + 0.5 * soft + 0.5 * partial + 0.3 * len(unclear)) / n
    else:
        coverage = None
        notes.append("no requirements section found; scored on the whole description")

    # 2. Tech overlap across the whole description: 25 points.
    # Share of technical asks I have, scaled by how many there are, so a vague ad can't score high.
    hard = have_all - SOFT_TERMS
    weight = len(hard) + 1.5 * len(missing_all)
    depth = min(1.0, len(hard) / HARD_DEPTH)
    overlap = (len(hard) / weight if weight else 0) * (0.5 + 0.5 * depth)
    if coverage is None:
        coverage = overlap
    elif n < 3:
        # Too few lines extracted to trust on their own
        coverage = (n * coverage + (3 - n) * overlap) / 3

    # 3. Title fit: 20 points
    title_pts = next((pts for rx, pts, _ in FAMILIES if rx.search(job.title)), 0)
    if not title_pts and re.search(r"\bdata\b|\bai\b|\bml\b", job.title, re.I):
        title_pts = 10

    total = 55 * coverage + 25 * overlap + title_pts

    # Penalties
    title_missing = terms(job.title, _MISSING)
    if title_missing:
        total -= 25
        notes.append(f"title centres on a skill not in profile: {', '.join(sorted(title_missing))}")
    if SENIOR_TITLE.search(job.title):
        total -= 10
        notes.append("title is above current seniority")
    desirable_missing = set().union(*(terms(d, _MISSING) for d in desirable)) if desirable else set()

    job.score = max(0, min(100, round(total)))
    if essential_missing:
        job.score = min(job.score, ESSENTIAL_CAP)
        notes.append(f"missing essential: {', '.join(dict.fromkeys(essential_missing))} (score capped at {ESSENTIAL_CAP})")
    job.quals_met, job.quals_missed, job.quals_unclear = met, missed, unclear
    notes.append(f"matched skills: {', '.join(sorted(have_all)) or 'none'}")
    if missing_all:
        notes.append(f"skills asked for but not in profile: {', '.join(sorted(missing_all))}")
    if desirable_missing:
        notes.append(f"desirable only: {', '.join(sorted(desirable_missing))}")
    notes.append(f"suggested base CV: {base_cv_for(job.title)}")
    job.score_notes = notes
