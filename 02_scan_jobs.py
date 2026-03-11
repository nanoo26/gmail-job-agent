import base64
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, urlparse, unquote

#    -Windows CMD
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from cv_loader import compute_cv_boost, load_all_cvs, CV_FILES, load_cv_docx, load_cv_pdf

#  Claude integration (optional  activated when ANTHROPIC_API_KEY is set) 
# Load order: (a) OS environment variable, (b) .env file in project root.
# The key is NEVER hardcoded here  set it in .env or as a system env var.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()          # silently does nothing if .env is absent
except ImportError:
    pass                    # python-dotenv not installed; OS env only

_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_CLAUDE_ENABLED = False

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "on")

def _env_int(name: str, default: int | None) -> int | None:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    if parsed <= 0:
        return None
    return parsed

_CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()
_CLAUDE_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("CLAUDE_FALLBACK_MODELS", "claude-sonnet-4-20250514,claude-sonnet-4-6").split(",")
    if m.strip()
]
_CLAUDE_MAX_PER_RUN = _env_int("CLAUDE_MAX_PER_RUN", 5)   # None => no cap
_CLAUDE_DEBUG_STOP_EARLY = _env_flag("CLAUDE_DEBUG_STOP_EARLY", True)
_SCAN_ONLY_NEW = _env_flag("SCAN_ONLY_NEW", True)
_SCAN_LIMIT = _env_int("SCAN_LIMIT", 60)
_SCAN_DEBUG_MODE = _env_flag("SCAN_DEBUG_MODE", False)
_SCAN_DEBUG_LIMIT = _env_int("SCAN_DEBUG_LIMIT", 20)
_SCAN_FETCH_BUFFER = _env_int("SCAN_FETCH_BUFFER", 80)
_SCAN_DAYS_BACK = _env_int("SCAN_DAYS_BACK", 90)
_SCAN_FROM_YEAR_START = _env_flag("SCAN_FROM_YEAR_START", False)
_SCAN_MAX_RESULTS = _env_int("SCAN_MAX_RESULTS", 160)
if _ANTHROPIC_API_KEY:
    try:
        import anthropic as _anthropic_mod
        _CLAUDE_ENABLED = True
        print(f"[claude] using model: {_CLAUDE_MODEL}")
    except ImportError:
        print("  [claude] anthropic package not installed  Claude analysis disabled.")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# מקורות טובי

ALLOW_FROM = [
    "alljob.co.il",
    "drushim.co.il",
    "linkedin.com",
    "tnuva.co.il",
    "ies.gov.il",
]

BLOCK_FROM = [
    "updates.ubisoft.com",
    "nianticlabs.com",
    "email.claude.com",
]

LINKEDIN_SUBJECT_BLOCK = [
    "צפה בפרופיל שלך",
    "צפו בפרופיל שלך",
    "יש פוסט חדש בשבילך",
    "הוסף",
    "הופעת השבוע",
    "מקבלים תשומת לב",
    "חיבורים משותפים",
    "מחשבותיהם על linkedin",
]

LINKEDIN_JOB_HINTS = [
    "התראות עבודה",
    "משרות חדשות",
    "צפייה במשרה",
    "שלחו מועמדות",
    "Job Alert",
    "jobs/view",
]

TAG_RE = re.compile(r"<[^>]+>")
STYLE_RE = re.compile(r"<style.*?>.*?</style>", re.IGNORECASE | re.DOTALL)
SCRIPT_RE = re.compile(r"<script.*?>.*?</script>", re.IGNORECASE | re.DOTALL)
CSS_BLOCK_RE = re.compile(r"@media[^{]*\{.*?\}\s*", re.IGNORECASE | re.DOTALL)

HREF_RE = re.compile(r'href=["\']([^"\']{10,})["\']', re.IGNORECASE)
BARE_URL_RE = re.compile(r'https?://[^\s<>"\']{10,}', re.IGNORECASE)
JOB_LINK_PATTERNS = [
    r'linkedin\.com/(?:comm/)?jobs/view/',
    r'alljobs\.co\.il/',
    r'drushim\.co\.il/job/',
]

IT_KEYWORDS = {
    "strong": [
        "helpdesk", "service desk", "it support", "system administrator", "sysadmin",
        "active directory", "ad ", "microsoft 365", "office 365", "intune", "sccm",
        "windows server", "networking", "vpn", "dns", "dhcp", "ticket", "jira", "zendesk",
        "תמיכת it", "תמיכה טכנית", "הלפדסק", "מחשוב", "מנהל רשת", "מנהל מערכת", "אופיס 365", "אקטיב דיירקטורי",
    ],
    "weak": ["windows", "microsoft", "support", "endpoint", "לינוקס", "שרתים", "תקשורת", "מערכות מידע"],
}
OPS_KEYWORDS = {
    "strong": [
        "operations manager", "production manager", "plant manager", "kpi", "lean", "six sigma",
        "supply chain", "planning", "scheduler", "logistics", "process", "continuous improvement",
        "\u05de\u05e0\u05d4\u05dc \u05ea\u05e4\u05e2\u05d5\u05dc", "\u05e1\u05de\u05e0\u05db\u05dc \u05ea\u05e4\u05e2\u05d5\u05dc", "\u05de\u05e0\u05d4\u05dc \u05d9\u05d9\u05e6\u05d5\u05e8", "\u05de\u05e0\u05d4\u05dc \u05de\u05e4\u05e2\u05dc", "\u05dc\u05d5\u05d2\u05d9\u05e1\u05d8\u05d9\u05e7\u05d4", "\u05e9\u05e8\u05e9\u05e8\u05ea \u05d0\u05e1\u05e4\u05e7\u05d4",
        "תכנון", "לוחות זמנים", "מצוינות תפעולית", "מדדי kpi", "רצפת ייצור",
    ],
    "weak": ["operations", "production", "תפעול", "ייצור", "תעשייה", "תכנון ובקרה", "מחסנים"],
}
MAINT_KEYWORDS = {
    "strong": [
        "maintenance manager", "facilities", "facility manager", "technician", "hvac", "electrician",
        "preventive maintenance", "cmms", "mechanical", "electromechanical",
        "אחזקה", "איש אחזקה", "מנהל אחזקה", "טכנאי", "חשמלאי", "מיזוג", "מערכות מיזוג",
        "אחזקה מונעת", "אלקטרומכניקה", "מכונאות",
    ],
    "weak": ["maintenance", "facility", "טכני", "מכונות", "תשתיות", "תחזוקה"],
}

def gmail_service():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("gmail", "v1", credentials=creds)

def extract_headers(headers):
    h = {x["name"].lower(): x["value"] for x in headers}
    return h.get("from", ""), h.get("subject", ""), h.get("date", "")

def decode_body(payload):
    # prefer text/plain
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                data = part["body"]["data"]
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        # fallback to text/html
        for part in payload["parts"]:
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                data = part["body"]["data"]
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""

def decode_html_body(payload) -> str:
    """מחזיר ת גוף ה-HTML של המייל (לצורך חילוץ קישורי בלבד)."""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""


def html_to_text(s: str) -> str:
    s = unescape(s)
    s = STYLE_RE.sub(" ", s)
    s = SCRIPT_RE.sub(" ", s)
    s = CSS_BLOCK_RE.sub(" ", s)
    s = TAG_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s



def _collect_job_link_candidates(html_body: str) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []

    for href in HREF_RE.findall(html_body):
        clean = unescape(href).strip()
        if not clean:
            continue
        for pat in JOB_LINK_PATTERNS:
            if re.search(pat, clean, re.IGNORECASE):
                if clean not in seen:
                    seen.add(clean)
                    candidates.append(clean)
                break

    for url in BARE_URL_RE.findall(html_body):
        clean = unescape(url).strip()
        if not clean:
            continue
        for pat in JOB_LINK_PATTERNS:
            if re.search(pat, clean, re.IGNORECASE):
                if clean not in seen:
                    seen.add(clean)
                    candidates.append(clean)
                break

    return candidates


def _extract_alljobs_job_id_from_value(value: str) -> str:
    if not value:
        return ""
    decoded = unquote(str(value))
    m = re.search(r'(\d{4,})', decoded)
    return m.group(1) if m else ""


def _normalize_alljobs_url_value(url: str) -> str:
    cleaned = unescape((url or "").strip())
    # Some emails store query delimiters as HTML entities.
    for _ in range(2):
        if "&amp;" in cleaned:
            cleaned = cleaned.replace("&amp;", "&")
    return cleaned


def _extract_alljobs_job_id(url: str) -> str:
    cleaned = _normalize_alljobs_url_value(url)
    if not cleaned:
        return ""

    try:
        parsed = urlparse(cleaned)
        params = parse_qs(parsed.query, keep_blank_values=True)
        for key, values in params.items():
            key_norm = key.lower().replace("amp;", "")
            if key_norm == 'jobid' and values:
                jid = _extract_alljobs_job_id_from_value(values[0])
                if jid:
                    return jid
            if values:
                for raw_val in values:
                    raw_decoded = unquote(str(raw_val))
                    m_nested = re.search(r'jobid=([^&#]+)', raw_decoded, re.IGNORECASE)
                    if m_nested:
                        jid = _extract_alljobs_job_id_from_value(m_nested.group(1))
                        if jid:
                            return jid
    except Exception:
        pass

    m = re.search(r'[?&]jobid=([^&#]+)', cleaned, re.IGNORECASE)
    if m:
        jid = _extract_alljobs_job_id_from_value(m.group(1))
        if jid:
            return jid

    return ""


def resolve_job_url(raw_link: str) -> tuple[str, str]:
    raw = _normalize_alljobs_url_value(raw_link)
    if not raw:
        return "", "unresolved"

    lower = raw.lower()
    if 'alljobs.co.il' in lower:
        job_id = _extract_alljobs_job_id(raw)
        if job_id:
            direct = f"https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID={job_id}"
            return direct, "resolved"
        if '/user/mailsredirect/' in lower or lower.rstrip('/') in ("https://www.alljobs.co.il", "http://www.alljobs.co.il"):
            # Better UX than opening the homepage when redirect token is stale.
            return "https://www.alljobs.co.il/User/JobsFeed/", "resolved"
        if _url_fingerprint(raw):
            return raw, "resolved"
        return raw, "unresolved"

    if _url_fingerprint(raw):
        return raw, "resolved"

    return raw, "unresolved"


def extract_job_link_info(html_body: str) -> tuple[str, str, str]:
    candidates = _collect_job_link_candidates(html_body)
    if not candidates:
        return "", "", "unresolved"

    for raw in candidates:
        resolved_url, status = resolve_job_url(raw)
        if status == "resolved":
            return raw, resolved_url, status

    raw = candidates[0]
    resolved_url, status = resolve_job_url(raw)
    return raw, resolved_url, status


def extract_job_url(html_body: str) -> str:
    _, resolved_url, _ = extract_job_link_info(html_body)
    return resolved_url


ANCHOR_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

#  -    

BOILERPLATE_RE = re.compile(
    r'view\s+(?:in\s+browser|online|job\s+online|email)|unsubscribe|'
    r'privacy\s+policy|terms\s+of\s+use|email\s+was\s+sent|'
    r'click\s+here\s+to\s+view|צפ(?:ייה|יה)\s+בדפדפן|'
    r'הסר\s+מהרשימה|לצפייה\s+באימייל|לביטול\s+המנוי',
    re.IGNORECASE,
)



def _url_fingerprint(url: str) -> str:
    """   -URL   ( LinkedIn / AllJobs / Drushim)."""
    # LinkedIn: /jobs/view/1234567
    m = re.search(r'/jobs/view/(\d+)', url)
    if m:
        return m.group(1)
    # Drushim: /job/1234/
    m = re.search(r'drushim\.co\.il/job/(\d+)', url, re.IGNORECASE)
    if m:
        return m.group(1)
    # AllJobs: ?JobID=1234 (query param)   
    m = re.search(r'[?&]job[iI][dD]=(\d+)', url, re.IGNORECASE)
    if m:
        return m.group(1)
    # AllJobs: /singleDescription/1234/ (path fallback)
    m = re.search(r'alljobs\.co\.il/[^?#]*?/(\d+)', url, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def _base_url(url: str) -> str:
    """מסיר scheme + query + fragment → ש מנורמל להשווה."""
    return re.sub(r'^https?://', '', url.split('?')[0].split('#')[0]).rstrip('/')


def extract_job_title(html_body: str, job_url: str) -> str:
    """מחלץ ת ש המשרה מהנקור שמצביע על job_url."""
    fp = _url_fingerprint(job_url)

    # שלב 1 — התמה לפי fingerprint (ID ספרתי)
    if fp:
        for m in ANCHOR_RE.finditer(html_body):
            if fp in m.group(1):
                title = re.sub(r"\s+", " ", TAG_RE.sub("", m.group(2))).strip()
                title = unescape(title)
                if 4 <= len(title) <= 150:
                    return title

    # שלב 2 — fallback: התמה לפי base-URL מל (לל query/fragment)
    if job_url:
        base = _base_url(job_url)
        for m in ANCHOR_RE.finditer(html_body):
            if base and _base_url(m.group(1)) == base:
                title = re.sub(r"\s+", " ", TAG_RE.sub("", m.group(2))).strip()
                title = unescape(title)
                if 4 <= len(title) <= 150:
                    return title

    return ""


def clean_snippet(text: str, job_title: str, length: int = 260) -> str:
    """     ."""
    #   -
    lines = [ln for ln in text.splitlines() if not BOILERPLATE_RE.search(ln)]
    cleaned = re.sub(r"\s+", " ", " ".join(lines)).strip()

    # fallback:  הסינון הותיר פחות מ-30 תווי, חזור לטקסט המקורי
    if len(cleaned) < 30:
        cleaned = re.sub(r"\s+", " ", text).strip()

    if job_title and cleaned:
        idx = cleaned.lower().find(job_title.lower())
        if idx >= 0:
            start = max(0, idx - 40)
            return cleaned[start:start + length].strip()

    return cleaned[:length]


def domain_in(value: str, domains: list[str]) -> bool:
    v = value.lower()
    return any(d.lower() in v for d in domains)

def is_linkedin_noise(sender: str, subject: str, body: str) -> bool:
    s = sender.lower()
    if "linkedin.com" not in s:
        return False

    subj = (subject or "").lower()
    if any(x.lower() in subj for x in LINKEDIN_SUBJECT_BLOCK):
        #  יש רמז חזק שזה כן משרה, ל לחסו
        blob = f"{subject} {body}".lower()
        if any(h.lower() in blob for h in LINKEDIN_JOB_HINTS):
            return False
        return True

    #  זה LinkedIn "messages/notifications" בלי רמז עבודה, לרוב רעש
    if ("messages-noreply@linkedin.com" in s or "notifications-noreply@linkedin.com" in s):
        blob = f"{subject} {body}".lower()
        if not any(h.lower() in blob for h in LINKEDIN_JOB_HINTS):
            return True

    return False

def score_track(subject: str, text: str, lex: dict) -> int:
    # משקל כפול למה שמופיע בנוש
    subj = (subject or "").lower()
    blob = (text or "").lower()

    strong = lex["strong"]
    weak = lex["weak"]

    score = 0
    for k in strong:
        kl = k.lower()
        if kl in subj:
            score += 18
        if kl in blob:
            score += 10
    for k in weak:
        kl = k.lower()
        if kl in subj:
            score += 8
        if kl in blob:
            score += 4

    #  100
    return min(100, score)


def analyze_with_claude(cv_text: str, job_subject: str, job_snippet: str, job_title: str) -> dict:
    _empty = {
        "match_pct": 0, "claude_analysis": "", "claude_error": "",
        "claude_raw_response": "", "claude_cv_track": "",
    }
    if not _CLAUDE_ENABLED:
        return _empty

    client = _anthropic_mod.Anthropic(api_key=_ANTHROPIC_API_KEY)
    job_info = f"כותרת: {job_title or job_subject}\n\nתיאור:\n{job_snippet}"
    cv_short = cv_text[:3000]

    prompt = f"""You are an expert Israeli recruiter. Analyze the fit between the CV and the job posting.

CV:
{cv_short}

---

Job Posting:
{job_info}

---

Respond with ONLY valid JSON. No markdown, no code fences, no text before or after the JSON.
Output MUST be a single-line minified JSON object.

Use exactly this schema:
{{
  "match_pct": 75,
  "recommended_cv_track": "IT",
  "strengths": ["strength 1", "strength 2"],
  "gaps": ["gap 1"],
  "recommendation": "one sentence",
  "reasoning": "one sentence"
}}

Rules:
- match_pct: integer 0-100
- recommended_cv_track: exactly one of ["IT", "תפעול", "אחזקה"]
- strengths: max 3 short items (each up to 60 chars)
- gaps: max 2 short items (each up to 60 chars)
- recommendation, reasoning: very short strings (each up to 120 chars)
- Avoid using double-quote characters inside values.
- Output ONLY the JSON object, nothing else."""

    def _short_api_error(err: Exception) -> str:
        status = getattr(err, "status_code", "")
        body = getattr(err, "body", "")
        if not body:
            resp = getattr(err, "response", None)
            body = getattr(resp, "text", "") if resp is not None else ""
        detail = re.sub(r"\s+", " ", str(body or err)).strip()
        prefix = f"status={status} " if status else ""
        return f"{prefix}{detail}"[:240]

    def _strip_code_fence(text: str) -> str:
        out = (text or "").strip()
        if out.startswith("```"):
            out = re.sub(r"^```[a-z]*\n?", "", out, flags=re.IGNORECASE)
            out = re.sub(r"\n?```$", "", out).strip()
        return out

    def _extract_list_field(text: str, field: str, limit: int) -> list[str]:
        m = re.search(rf'["\']?{field}["\']?\s*:\s*\[(.*?)\]', text, re.IGNORECASE | re.DOTALL)
        if not m:
            return []
        inner = m.group(1)
        vals = [v.strip() for v in re.findall(r'"([^"]{1,120})"|\'([^\']{1,120})\'', inner)]
        flattened: list[str] = []
        for pair in vals:
            if isinstance(pair, tuple):
                flattened.append((pair[0] or pair[1]).strip())
            else:
                flattened.append(str(pair).strip())
        return [v for v in flattened if v][:limit]

    def _extract_text_field(text: str, field: str, max_len: int = 240) -> str:
        # Try strict quoted extraction first (double/single quotes).
        strict = re.search(
            rf'["\']?{field}["\']?\s*:\s*(?:"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\')',
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if strict:
            value = strict.group(1) or strict.group(2) or ""
            return value.strip()[:max_len]

        # Fallback: tolerate truncated strings until line break/comma/brace.
        loose = re.search(
            rf'["\']?{field}["\']?\s*:\s*([^\n\r,}}]{{1,{max_len}}})',
            text,
            re.IGNORECASE,
        )
        if not loose:
            return ""
        value = loose.group(1).strip().strip('"').strip("'").strip()
        return value[:max_len]

    def _extract_match_pct(text: str) -> int | None:
        patterns = [
            r'["\']?match_pct["\']?\s*[:=]\s*["\']?(\d{1,3})',
            r'["\']?match["\']?\s*[:=]\s*["\']?(\d{1,3})',
            r'["\']?score["\']?\s*[:=]\s*["\']?(\d{1,3})',
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if not m:
                continue
            try:
                value = int(m.group(1))
            except Exception:
                continue
            return max(0, min(100, value))
        return None

    def _normalize_track(value: str) -> str:
        raw_val = str(value or "").strip()
        low = raw_val.lower()
        if raw_val == "IT" or low == "it":
            return "IT"
        if "תפעול" in raw_val or "ops" in low or "operation" in low:
            return "תפעול"
        if "אחזקה" in raw_val or "maint" in low or "facility" in low:
            return "אחזקה"
        return ""

    def _best_effort_parse(text: str) -> dict | None:
        if not text:
            return None

        normalized = (
            (text or "")
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2018", "'")
            .replace("\u2019", "'")
        )
        match_pct = _extract_match_pct(normalized)
        if match_pct is None:
            return None

        data: dict = {"match_pct": match_pct}
        m_track = re.search(r'["\']?recommended_cv_track["\']?\s*:\s*["\']([^"\']{1,40})["\']', normalized, re.IGNORECASE)
        if m_track:
            data["recommended_cv_track"] = m_track.group(1)
        data["strengths"] = _extract_list_field(normalized, "strengths", 3)
        data["gaps"] = _extract_list_field(normalized, "gaps", 2)
        rec = _extract_text_field(normalized, "recommendation", 240)
        if rec:
            data["recommendation"] = rec
        return data

    global _CLAUDE_MODEL
    models_to_try = [_CLAUDE_MODEL] + [m for m in _CLAUDE_FALLBACK_MODELS if m != _CLAUDE_MODEL]
    tried_errors = []

    for model_name in models_to_try:
        raw = ""
        try:
            message = client.messages.create(
                model=model_name,
                max_tokens=900,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = _strip_code_fence(message.content[0].text)

            parse_mode = "strict_json"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = _best_effort_parse(raw)
                if not data:
                    raise
                parse_mode = "best_effort"

            match_pct = int(data.get("match_pct", 0) or 0)
            match_pct = max(0, min(100, match_pct))

            cv_track = _normalize_track(str(data.get("recommended_cv_track", "")))
            strengths = data.get("strengths", [])
            gaps = data.get("gaps", [])
            rec = str(data.get("recommendation", ""))
            parts = []
            if parse_mode != "strict_json":
                parts.append("⚠ parsed via fallback")
            if rec:
                parts.append(rec)
            if strengths:
                parts.append("חוזקות: " + ", ".join(str(sv) for sv in strengths[:3]))
            if gaps:
                parts.append("פערים: " + ", ".join(str(gv) for gv in gaps[:2]))

            if model_name != _CLAUDE_MODEL:
                print(f"  [claude] switched model: {model_name}")
                _CLAUDE_MODEL = model_name

            return {
                "match_pct": match_pct,
                "claude_analysis": " | ".join(parts),
                "claude_error": "",
                "claude_raw_response": raw[:500],
                "claude_cv_track": cv_track,
            }

        except _anthropic_mod.NotFoundError as e:
            tried_errors.append(f"{model_name}: {_short_api_error(e)}")
            continue
        except _anthropic_mod.APIError as e:
            return {**_empty, "claude_error": f"{model_name}: {_short_api_error(e)}", "claude_raw_response": raw[:200]}
        except json.JSONDecodeError as e:
            return {**_empty, "claude_error": f"JSON parse error: {e} | raw[:80]={raw[:80]}", "claude_raw_response": raw[:500]}
        except Exception as e:
            return {**_empty, "claude_error": f"{model_name}: {str(e)[:200]}", "claude_raw_response": raw[:200]}

    if tried_errors:
        return {**_empty, "claude_error": " | ".join(tried_errors)[:260], "claude_raw_response": ""}
    return _empty


def recommend_cv(best_track: str) -> str:
    if best_track == "IT":
        return "קורות חיים - IT"
    if best_track == "תפעול":
        return "קורות חיים - תפעול"
    return "שלום חכמון - אחזקה"


def _extract_gmail_msg_id(gmail_link: str) -> str:
    link = (gmail_link or "").strip()
    if not link:
        return ""
    msg_id = link.rstrip("/").rsplit("/", 1)[-1]
    return msg_id.split("?", 1)[0].strip()


def _load_existing_rows_and_ids(path: str) -> tuple[list[dict], set[str]]:
    rows: list[dict] = []
    ids: set[str] = set()
    if not os.path.exists(path):
        return rows, ids

    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
                msg_id = (row.get("gmail_msg_id", "") or "").strip()
                if not msg_id:
                    msg_id = _extract_gmail_msg_id(str(row.get("link", "")))
                if msg_id:
                    ids.add(msg_id)
    except Exception as e:
        print(f"  [scan] warning: could not read existing CSV: {e}")

    return rows, ids


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return default


def _claude_unique_key(row: dict) -> str:
    for field in ("job_id", "gmail_msg_id"):
        value = str(row.get(field, "") or "").strip()
        if value:
            return f"{field}:{value}"
    title = str(row.get("job_title", "") or row.get("subject", "")).strip().lower()
    if title:
        return f"title:{title[:160]}"
    return f"fallback:{str(row.get('link', '')).strip()}"


def _top_claude_rows_unique(rows: list[dict], limit: int = 5) -> list[dict]:
    ranked = sorted(
        rows,
        key=lambda r: (_safe_int(r.get("claude_match_pct", 0)), _safe_int(r.get("final_score", 0))),
        reverse=True,
    )
    seen: set[str] = set()
    top_rows: list[dict] = []
    for row in ranked:
        key = _claude_unique_key(row)
        if key in seen:
            continue
        seen.add(key)
        top_rows.append(row)
        if len(top_rows) >= limit:
            break
    return top_rows


def _fetch_messages_paginated(service, query: str, fetch_limit: int | None) -> list[dict]:
    """Fetch Gmail messages with pagination up to fetch_limit (or all when None)."""
    messages: list[dict] = []
    page_token: str | None = None
    while True:
        page_size = 500
        if fetch_limit is not None:
            remaining = fetch_limit - len(messages)
            if remaining <= 0:
                break
            page_size = min(page_size, remaining)

        req = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=page_size,
            pageToken=page_token,
        )
        res = req.execute()
        batch = res.get("messages", []) or []
        messages.extend(batch)
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return messages



def main(days_back=90, max_results=120, only_inbox=True):
    out_file = "job_emails.csv"
    existing_rows: list[dict] = []
    existing_ids: set[str] = set()
    if _SCAN_ONLY_NEW:
        existing_rows, existing_ids = _load_existing_rows_and_ids(out_file)

    service = gmail_service()
    after = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y/%m/%d")

    print("\nטוען קורות חיים...")
    cv_data = load_all_cvs()

    cv_texts: dict = {}
    if _CLAUDE_ENABLED:
        print("  [claude] טוען טקסט קורות חיים לניתוח Claude...")
        from pathlib import Path
        for track, file_list in CV_FILES.items():
            parts = []
            for fp in file_list:
                ext = Path(fp).suffix.lower()
                text = load_cv_pdf(fp) if ext == ".pdf" else load_cv_docx(fp)
                if text.strip():
                    parts.append(text)
            cv_texts[track] = "\n".join(parts)
        print("  [claude] Claude enabled — ניתוח לכל משרה יופעל\n")
    else:
        print("  [claude] ANTHROPIC_API_KEY לא הוגדר — ניתוח Claude מושבת\n")

    sender_query = " OR ".join([f"from:{d}" for d in ALLOW_FROM])
    keywords_query = '("משרה" OR "דרושים" OR "התראת עבודה" OR "מועמדות" OR "ראיון" OR "Job Alert" OR "application" OR "position")'
    q_parts = [f"after:{after}", f"({sender_query} OR {keywords_query})"]
    if only_inbox:
        q_parts.append("in:inbox")
    query = " ".join(q_parts)

    scan_limit = _SCAN_LIMIT if _SCAN_LIMIT is not None else max_results
    if _SCAN_DEBUG_MODE and _SCAN_DEBUG_LIMIT is not None:
        scan_limit = _SCAN_DEBUG_LIMIT if scan_limit is None else min(scan_limit, _SCAN_DEBUG_LIMIT)

    fetch_limit = max_results
    if scan_limit is not None:
        buffer = _SCAN_FETCH_BUFFER or 0
        required = max(scan_limit, scan_limit + buffer)
        fetch_limit = required if fetch_limit is None else min(fetch_limit, required)

    msgs = _fetch_messages_paginated(service, query, fetch_limit)
    print(f"[scan] fetched {len(msgs)} candidate emails (fetch_limit={fetch_limit or 'ALL'})")

    new_rows = []
    scanned_emails = 0
    skipped_existing = 0
    _claude_calls = 0
    claude_analyzed_rows: list[dict] = []

    for i, m in enumerate(msgs):
        msg_id_hint = (m.get("id") or "").strip()
        if _SCAN_ONLY_NEW and msg_id_hint and msg_id_hint in existing_ids:
            skipped_existing += 1
            continue

        if scan_limit is not None and scanned_emails >= scan_limit:
            break
        scanned_emails += 1

        msg = service.users().messages().get(userId="me", id=msg_id_hint, format="full").execute()
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        sender, subject, date = extract_headers(headers)

        if domain_in(sender, BLOCK_FROM):
            continue

        raw_body = decode_body(payload)
        clean_body = html_to_text(raw_body)
        html_body = decode_html_body(payload)
        raw_email_link, job_url, link_status = extract_job_link_info(html_body)
        job_title = extract_job_title(html_body, raw_email_link or job_url)

        if is_linkedin_noise(sender, subject, clean_body):
            continue

        it_score = score_track(subject, clean_body, IT_KEYWORDS)
        ops_score = score_track(subject, clean_body, OPS_KEYWORDS)
        maint_score = score_track(subject, clean_body, MAINT_KEYWORDS)

        if max(it_score, ops_score, maint_score) < 10 and not domain_in(sender, ["drushim.co.il", "alljob.co.il"]):
            continue

        best = max(
            [("IT", it_score), ("תפעול", ops_score), ("אחזקה", maint_score)],
            key=lambda x: x[1],
        )[0]

        cv = recommend_cv(best)

        snippet = clean_snippet(clean_body, job_title)
        msg_id = msg_id_hint or msg.get("id", "")
        gmail_link = f"https://mail.google.com/mail/u/0/#all/{msg_id}"

        cv_tokens = set()
        cv_tokens |= cv_data.get(best, set())
        cv_tokens |= cv_data.get("כללי", set())
        cv_tokens |= cv_data.get("פרופיל", set())
        cv_boost, match_reasons = compute_cv_boost(cv_tokens, f"{subject} {job_title} {snippet}")
        top_score = max(it_score, ops_score, maint_score)

        claude_match_pct = 0
        claude_analysis = ""
        claude_error = ""
        claude_raw_resp = ""
        claude_cv_track = ""
        _run_claude = (
            _CLAUDE_ENABLED and
            (_CLAUDE_MAX_PER_RUN is None or _claude_calls < _CLAUDE_MAX_PER_RUN)
        )
        if _run_claude:
            cv_text_for_claude = cv_texts.get(best, cv_texts.get("כללי", ""))
            print(f"  [{i+1}/{len(msgs)}] Claude [{_claude_calls+1}]: {(job_title or subject)[:55]}")
            result = analyze_with_claude(cv_text_for_claude, subject, snippet, job_title)
            _claude_calls += 1
            claude_match_pct = result["match_pct"]
            claude_analysis = result["claude_analysis"]
            claude_error = result["claude_error"]
            claude_raw_resp = result["claude_raw_response"]
            claude_cv_track = result["claude_cv_track"]
            print(f"  [claude-debug] match_pct={claude_match_pct} | track={claude_cv_track or best} | err={claude_error[:80] if claude_error else 'ok'}")

        if _CLAUDE_ENABLED and claude_match_pct > 0:
            final_score = min(100, int(top_score * 0.3 + claude_match_pct * 0.7))
        else:
            final_score = min(100, top_score + cv_boost)

        new_rows.append({
            "date": date,
            "from": sender,
            "subject": subject,
            "job_title": job_title,
            "snippet": snippet,
            "link": gmail_link,
            "gmail_msg_id": msg_id,
            "it_score": it_score,
            "ops_score": ops_score,
            "maint_score": maint_score,
            "best_track": best,
            "cv_recommendation": cv,
            "top_score": top_score,
            "cv_boost": cv_boost,
            "final_score": final_score,
            "match_reasons": match_reasons,
            "raw_email_link": raw_email_link,
            "job_url": job_url,
            "link_status": link_status,
            "claude_match_pct": claude_match_pct,
            "claude_analysis": claude_analysis,
            "claude_cv_track": claude_cv_track,
            "claude_error": claude_error,
            "claude_raw_response": claude_raw_resp,
        })

        if _run_claude:
            claude_analyzed_rows.append(new_rows[-1])

        if (
            _CLAUDE_ENABLED
            and _CLAUDE_DEBUG_STOP_EARLY
            and _CLAUDE_MAX_PER_RUN is not None
            and _claude_calls >= _CLAUDE_MAX_PER_RUN
        ):
            print(f"  [claude-debug] reached {_CLAUDE_MAX_PER_RUN} Claude-eligible jobs; stopping early for verification.")
            break

    rows = new_rows
    if _SCAN_ONLY_NEW and existing_rows:
        merged_without_id = []
        merged_by_id: dict[str, dict] = {}

        for row in existing_rows:
            mid = (row.get("gmail_msg_id", "") or "").strip() or _extract_gmail_msg_id(str(row.get("link", "")))
            if mid:
                merged_by_id[mid] = row
            else:
                merged_without_id.append(row)

        for row in new_rows:
            mid = (row.get("gmail_msg_id", "") or "").strip() or _extract_gmail_msg_id(str(row.get("link", "")))
            if mid:
                merged_by_id[mid] = row
            else:
                merged_without_id.append(row)

        rows = merged_without_id + list(merged_by_id.values())

    def _score_value(r: dict) -> int:
        try:
            return int(float(r.get("final_score", 0) or 0))
        except Exception:
            return 0

    rows.sort(key=_score_value, reverse=True)

    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date", "from", "subject", "job_title", "snippet", "link", "gmail_msg_id",
                "it_score", "ops_score", "maint_score",
                "best_track", "cv_recommendation",
                "top_score", "cv_boost", "final_score", "match_reasons", "raw_email_link", "job_url", "link_status",
                "claude_match_pct", "claude_analysis", "claude_cv_track",
                "claude_error", "claude_raw_response",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ Exported: {out_file}  ({len(rows)} rows)\n")
    print(f"[scan] scanned {scanned_emails} emails, skipped {skipped_existing} existing, exported {len(rows)} rows")

    claude_calls_attempted = len(claude_analyzed_rows)
    claude_success_count = sum(1 for r in claude_analyzed_rows if not str(r.get("claude_error", "")).strip())
    claude_error_count = claude_calls_attempted - claude_success_count
    claude_positive_count = sum(1 for r in claude_analyzed_rows if _safe_int(r.get("claude_match_pct", 0)) > 0)
    claude_match_values = [_safe_int(r.get("claude_match_pct", 0)) for r in claude_analyzed_rows]
    claude_avg_match_pct = round(sum(claude_match_values) / len(claude_match_values), 2) if claude_match_values else 0
    claude_max_match_pct = max(claude_match_values) if claude_match_values else 0

    # Dataset snapshot (all exported rows), useful when incremental run had 0 fresh Claude calls.
    dataset_match_values = [_safe_int(r.get("claude_match_pct", 0)) for r in rows]
    dataset_positive_count = sum(1 for v in dataset_match_values if v > 0)
    dataset_avg_match_pct = round(sum(dataset_match_values) / len(dataset_match_values), 2) if dataset_match_values else 0
    dataset_max_match_pct = max(dataset_match_values) if dataset_match_values else 0
    top_5_claude_rows = _top_claude_rows_unique(rows, limit=5)

    if _CLAUDE_ENABLED:
        print("🏆 TOP 5 לפי התאמת Claude:\n")
        if top_5_claude_rows:
            for r in top_5_claude_rows:
                print(f"  {_safe_int(r.get('claude_match_pct', 0))}% | {(r.get('job_title') or r.get('subject', ''))[:55]}")
        else:
            print("  אין שורות Claude להצגה")

    print("[claude-summary] "
          f"scanned_emails={scanned_emails} "
          f"skipped_existing={skipped_existing} "
          f"total_exported_rows={len(rows)} "
          f"claude_calls_attempted={claude_calls_attempted} "
          f"claude_success_count={claude_success_count} "
          f"claude_error_count={claude_error_count} "
          f"claude_positive_count={claude_positive_count} "
          f"claude_avg_match_pct={claude_avg_match_pct} "
          f"claude_max_match_pct={claude_max_match_pct} "
          f"dataset_positive_count={dataset_positive_count} "
          f"dataset_avg_match_pct={dataset_avg_match_pct} "
          f"dataset_max_match_pct={dataset_max_match_pct}")

    summary_payload = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "scan_mode": "only_new" if _SCAN_ONLY_NEW else "full",
        "scan_days_back": days_back,
        "scan_limit": scan_limit,
        "scan_fetch_limit": fetch_limit,
        "scan_max_results": max_results,
        "claude_max_per_run": _CLAUDE_MAX_PER_RUN,
        "scanned_emails": scanned_emails,
        "skipped_existing": skipped_existing,
        "total_exported_rows": len(rows),
        "claude_calls_attempted": claude_calls_attempted,
        "claude_success_count": claude_success_count,
        "claude_error_count": claude_error_count,
        "claude_positive_count": claude_positive_count,
        "claude_avg_match_pct": claude_avg_match_pct,
        "claude_max_match_pct": claude_max_match_pct,
        "dataset_positive_count": dataset_positive_count,
        "dataset_avg_match_pct": dataset_avg_match_pct,
        "dataset_max_match_pct": dataset_max_match_pct,
        "top_5_claude_rows": [
            {
                "title": (r.get("job_title") or r.get("subject", ""))[:180],
                "subject": r.get("subject", "")[:180],
                "match_pct": _safe_int(r.get("claude_match_pct", 0)),
                "track": r.get("claude_cv_track") or r.get("best_track", ""),
                "error": str(r.get("claude_error", "") or "")[:120],
            }
            for r in top_5_claude_rows
        ],
    }
    summary_file = os.path.join(os.path.dirname(__file__), "scan_run_summary.json")
    try:
        with open(summary_file, "w", encoding="utf-8") as sf:
            json.dump(summary_payload, sf, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[scan] warning: could not write scan_run_summary.json: {e}")

    # Append per-run history for longitudinal tracking.
    history_file = os.path.join(os.path.dirname(__file__), "scan_run_history.jsonl")
    try:
        with open(history_file, "a", encoding="utf-8") as hf:
            hf.write(json.dumps(summary_payload, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[scan] warning: could not append scan_run_history.jsonl: {e}")


if __name__ == "__main__":
    run_days_back = _SCAN_DAYS_BACK if _SCAN_DAYS_BACK is not None else 90
    if _SCAN_FROM_YEAR_START:
        now_utc = datetime.now(timezone.utc)
        jan1_utc = datetime(now_utc.year, 1, 1, tzinfo=timezone.utc)
        run_days_back = max(1, (now_utc - jan1_utc).days + 1)
        print(f"[scan] using start-of-year window ({now_utc.year}): {run_days_back} days back")
    else:
        print(f"[scan] using window: {run_days_back} days back")
    main(days_back=run_days_back, max_results=_SCAN_MAX_RESULTS, only_inbox=True)



