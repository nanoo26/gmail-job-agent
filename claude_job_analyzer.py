"""
claude_job_analyzer.py
======================
שיפור למערכת סריקת מיילים — משלב ניתוח Claude API לכל משרה.

שימוש:
    python claude_job_analyzer.py

דרישות:
    pip install anthropic python-docx PyMuPDF google-auth google-api-python-client

הגדרות נדרשות:
    1. קובץ token.json של Gmail (כמו קודם)
    2. משתנה סביבה: ANTHROPIC_API_KEY=sk-ant-...
       אפשרות נוספת: קובץ .env עם ANTHROPIC_API_KEY=...
       אין להכניס את המפתח ישירות בקוד.
"""

import os
import base64
import csv
import re
import sys
import logging
from datetime import datetime, timedelta, timezone
from html import unescape

# ── טעינת מפתח API בצורה מאובטחת ──
# סדר עדיפות: (א) משתנה סביבה, (ב) קובץ .env
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ============================================================
# הגדרת נתיבי קורות חיים — עדכן לפי מיקום הקבצים אצלך:
# ============================================================
CV_FILES = {
    "IT":     "קורות_חיים__IT.pdf",
    "תפעול":  "קורות_חייםתפעול.pdf",
    "אחזקה":  "שלום_חכמון_-_אחזקה.docx",
    "כללי":   "קורות_חיים_-_כללי.docx",
}

# תמיכה בפלט עברי ב-Windows CMD
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# ייבואים
# ============================================================
try:
    import anthropic
except ImportError:
    log.error("חסר: pip install anthropic")
    sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None
    log.warning("PyMuPDF לא מותקן — קבצי PDF לא יוכלו להיקרא. pip install PyMuPDF")

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None
    log.warning("python-docx לא מותקן — קבצי Word לא יוכלו להיקרא. pip install python-docx")

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

ALLOW_FROM = ["alljob.co.il", "drushim.co.il", "linkedin.com", "tnuva.co.il", "ies.gov.il"]
BLOCK_FROM = ["updates.ubisoft.com", "nianticlabs.com", "email.claude.com"]

LINKEDIN_SUBJECT_BLOCK = [
    "צפה בפרופיל שלך", "צפו בפרופיל שלך", "יש פוסט חדש בשבילך",
    "הוסף", "הופעת השבוע", "מקבלים תשומת לב", "חיבורים משותפים",
]
LINKEDIN_JOB_HINTS = [
    "התראות עבודה", "משרות חדשות", "צפייה במשרה", "שלחו מועמדות",
    "Job Alert", "jobs/view",
]

TAG_RE     = re.compile(r"<[^>]+>")
STYLE_RE   = re.compile(r"<style.*?>.*?</style>", re.IGNORECASE | re.DOTALL)
SCRIPT_RE  = re.compile(r"<script.*?>.*?</script>", re.IGNORECASE | re.DOTALL)
HREF_RE    = re.compile(r'href=["\']([^"\']{10,})["\']', re.IGNORECASE)
BARE_URL_RE = re.compile(r'https?://[^\s<>"\']{10,}', re.IGNORECASE)
ANCHOR_RE  = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)

JOB_LINK_PATTERNS = [
    r'linkedin\.com/(?:comm/)?jobs/view/',
    r'alljobs\.co\.il/',
    r'drushim\.co\.il/job/',
]

IT_KEYWORDS = {
    "strong": ["helpdesk","service desk","it support","system administrator","sysadmin",
               "active directory","microsoft 365","office 365","intune","sccm",
               "windows server","networking","vpn","dns","dhcp","ticket","jira","zendesk",
               "תמיכת it","תמיכה טכנית","הלפדסק","מחשוב","מנהל רשת","מנהל מערכת"],
    "weak": ["windows","microsoft","support","endpoint","לינוקס","שרתים","תקשורת"],
}
OPS_KEYWORDS = {
    "strong": ["operations manager","production manager","kpi","lean","six sigma",
               "supply chain","planning","scheduler","logistics","process",
               "מנהל תפעול","סמנכ\"ל תפעול","מנהל ייצור","לוגיסטיקה","שרשרת אספקה",
               "תכנון","מצוינות תפעולית"],
    "weak": ["operations","production","תפעול","ייצור","תעשייה","תכנון ובקרה"],
}
MAINT_KEYWORDS = {
    "strong": ["maintenance manager","facilities","facility manager","technician","hvac",
               "electrician","preventive maintenance","cmms","mechanical",
               "אחזקה","איש אחזקה","מנהל אחזקה","טכנאי","חשמלאי","מיזוג","אחזקה מונעת"],
    "weak": ["maintenance","facility","טכני","מכונות","תשתיות","תחזוקה"],
}

# ============================================================
# קריאת קורות חיים
# ============================================================

def read_pdf_text(path: str) -> str:
    if fitz is None:
        return ""
    try:
        doc = fitz.open(path)
        return " ".join(page.get_text() for page in doc)
    except Exception as e:
        log.warning(f"שגיאה בקריאת PDF {path}: {e}")
        return ""

def read_docx_text(path: str) -> str:
    if DocxDocument is None:
        return ""
    try:
        doc = DocxDocument(path)
        return " ".join(p.text for p in doc.paragraphs)
    except Exception as e:
        log.warning(f"שגיאה בקריאת DOCX {path}: {e}")
        return ""

def load_cv_texts() -> dict:
    """טוען את טקסט קורות החיים מהקבצים."""
    texts = {}
    for track, path in CV_FILES.items():
        if not os.path.exists(path):
            log.warning(f"קובץ לא נמצא: {path}")
            texts[track] = ""
            continue
        if path.lower().endswith(".pdf"):
            texts[track] = read_pdf_text(path)
        elif path.lower().endswith(".docx"):
            texts[track] = read_docx_text(path)
        else:
            texts[track] = ""
        log.info(f"נטען קורות חיים '{track}': {len(texts[track])} תווים")
    return texts

# ============================================================
# Gmail
# ============================================================

def gmail_service():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("gmail", "v1", credentials=creds)

def extract_headers(headers):
    h = {x["name"].lower(): x["value"] for x in headers}
    return h.get("from", ""), h.get("subject", ""), h.get("date", "")

def decode_body(payload):
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
        for part in payload["parts"]:
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""

def decode_html_body(payload) -> str:
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
    s = TAG_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def _url_fingerprint(url: str) -> str:
    m = re.search(r'/jobs/view/(\d+)', url)
    if m: return m.group(1)
    m = re.search(r'drushim\.co\.il/job/(\d+)', url, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r'[?&]job[iI][dD]=(\d+)', url, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r'alljobs\.co\.il/[^?#]*?/(\d+)', url, re.IGNORECASE)
    if m: return m.group(1)
    return ""

def extract_job_url(html_body: str) -> str:
    seen, candidates = set(), []
    for href in HREF_RE.findall(html_body):
        for pat in JOB_LINK_PATTERNS:
            if re.search(pat, href, re.IGNORECASE):
                if href not in seen:
                    seen.add(href); candidates.append(href)
                break
    for url in candidates:
        if _url_fingerprint(url):
            return url
    return candidates[0] if candidates else ""

def extract_job_title(html_body: str, job_url: str) -> str:
    fp = _url_fingerprint(job_url)
    if fp:
        for m in ANCHOR_RE.finditer(html_body):
            if fp in m.group(1):
                title = unescape(re.sub(r"\s+", " ", TAG_RE.sub("", m.group(2))).strip())
                if 4 <= len(title) <= 150:
                    return title
    return ""

def domain_in(value: str, domains: list) -> bool:
    v = value.lower()
    return any(d.lower() in v for d in domains)

def is_linkedin_noise(sender: str, subject: str, body: str) -> bool:
    if "linkedin.com" not in sender.lower():
        return False
    subj = (subject or "").lower()
    if any(x.lower() in subj for x in LINKEDIN_SUBJECT_BLOCK):
        blob = f"{subject} {body}".lower()
        if any(h.lower() in blob for h in LINKEDIN_JOB_HINTS):
            return False
        return True
    return False

def score_track(subject: str, text: str, lex: dict) -> int:
    subj = (subject or "").lower()
    blob = (text or "").lower()
    score = 0
    for k in lex["strong"]:
        kl = k.lower()
        if kl in subj: score += 18
        if kl in blob: score += 10
    for k in lex["weak"]:
        kl = k.lower()
        if kl in subj: score += 8
        if kl in blob: score += 4
    return min(100, score)

# ============================================================
# Claude API — ניתוח משרה
# ============================================================

def analyze_with_claude(cv_text: str, job_subject: str, job_snippet: str, job_title: str) -> dict:
    """
    שולח ל-Claude את קורות החיים + פרטי המשרה
    ומקבל ניתוח מפורט.
    מחזיר dict עם: match_pct, strengths, gaps, recommendation, summary
    """
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "YOUR_API_KEY_HERE":
        return {"error": "API Key לא הוגדר"}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    job_info = f"כותרת: {job_title or job_subject}\n\nתיאור:\n{job_snippet}"
    cv_short = cv_text[:3000] if len(cv_text) > 3000 else cv_text

    prompt = f"""אתה מומחה גיוס ישראלי. נתח את ההתאמה בין קורות החיים למשרה.

קורות חיים:
{cv_short}

---

פרטי המשרה:
{job_info}

---

החזר תשובה בדיוק בפורמט הזה (עברית):

אחוז התאמה: [מספר 0-100]%

חוזקות:
- [נקודה 1]
- [נקודה 2]
- [נקודה 3]

פערים:
- [נקודה 1]
- [נקודה 2]

המלצה: [משפט אחד — כן/לא/אולי כדאי להגיש, ולמה]

טיפ למכתב מקדים: [טיפ קצר אחד]"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()

        # חילוץ אחוז התאמה
        pct_match = re.search(r'אחוז התאמה[:\s]*(\d+)', raw)
        match_pct = int(pct_match.group(1)) if pct_match else 0

        return {
            "match_pct": match_pct,
            "claude_analysis": raw,
            "error": ""
        }
    except Exception as e:
        log.error(f"שגיאת Claude API: {e}")
        return {"match_pct": 0, "claude_analysis": "", "error": str(e)}

# ============================================================
# Main
# ============================================================

def main(days_back=90, max_results=120, only_inbox=True):
    log.info("מתחיל סריקה...")

    # טעינת קורות חיים
    cv_texts = load_cv_texts()

    # התחברות Gmail
    try:
        service = gmail_service()
    except Exception as e:
        log.error(f"שגיאה בהתחברות Gmail: {e}")
        sys.exit(1)

    after = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y/%m/%d")
    sender_query = " OR ".join([f"from:{d}" for d in ALLOW_FROM])
    keywords_query = '("משרה" OR "דרושים" OR "התראת עבודה" OR "מועמדות" OR "Job Alert")'
    q_parts = [f"after:{after}", f"({sender_query} OR {keywords_query})"]
    if only_inbox:
        q_parts.append("in:inbox")
    query = " ".join(q_parts)

    res = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    msgs = res.get("messages", [])
    log.info(f"נמצאו {len(msgs)} הודעות")

    rows = []
    for i, m in enumerate(msgs):
        try:
            msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
            payload = msg.get("payload", {})
            headers = payload.get("headers", [])
            sender, subject, date = extract_headers(headers)

            if domain_in(sender, BLOCK_FROM):
                continue

            raw_body  = decode_body(payload)
            html_body = decode_html_body(payload)
            clean_body = html_to_text(raw_body)
            job_url   = extract_job_url(html_body)
            job_title = extract_job_title(html_body, job_url)

            if is_linkedin_noise(sender, subject, clean_body):
                continue

            it_score    = score_track(subject, clean_body, IT_KEYWORDS)
            ops_score   = score_track(subject, clean_body, OPS_KEYWORDS)
            maint_score = score_track(subject, clean_body, MAINT_KEYWORDS)

            if max(it_score, ops_score, maint_score) < 10 and not domain_in(sender, ["drushim.co.il","alljob.co.il"]):
                continue

            best = max(
                [("IT", it_score), ("תפעול", ops_score), ("אחזקה", maint_score)],
                key=lambda x: x[1]
            )[0]

            snippet = clean_body[:300]
            msg_id = msg.get("id", "")
            gmail_link = f"https://mail.google.com/mail/u/0/#all/{msg_id}"

            # ניתוח Claude
            cv_text = cv_texts.get(best, cv_texts.get("כללי", ""))
            log.info(f"[{i+1}/{len(msgs)}] מנתח עם Claude: {subject[:50]}")
            claude_result = analyze_with_claude(cv_text, subject, snippet, job_title)

            top_score  = max(it_score, ops_score, maint_score)
            match_pct  = claude_result.get("match_pct", 0)
            # ציון סופי: שילוב של ציון מילות מפתח + ניתוח Claude
            final_score = int(top_score * 0.3 + match_pct * 0.7)

            rows.append({
                "date":            date,
                "from":            sender,
                "subject":         subject,
                "job_title":       job_title,
                "snippet":         snippet,
                "link":            gmail_link,
                "job_url":         job_url,
                "best_track":      best,
                "it_score":        it_score,
                "ops_score":       ops_score,
                "maint_score":     maint_score,
                "top_score":       top_score,
                "claude_match_pct": match_pct,
                "final_score":     final_score,
                "claude_analysis": claude_result.get("claude_analysis", ""),
                "claude_error":    claude_result.get("error", ""),
            })

        except Exception as e:
            log.warning(f"שגיאה בעיבוד הודעה {m['id']}: {e}")
            continue

    # מיון לפי ציון סופי
    rows.sort(key=lambda r: r["final_score"], reverse=True)

    out_file = "job_emails_claude.csv"
    fieldnames = [
        "date","from","subject","job_title","snippet","link","job_url",
        "best_track","it_score","ops_score","maint_score",
        "top_score","claude_match_pct","final_score",
        "claude_analysis","claude_error",
    ]
    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ נשמר: {out_file}  ({len(rows)} משרות)\n")
    print("🏆 TOP 5 משרות לפי התאמת Claude:\n")
    for r in rows[:5]:
        print(f"  {r['claude_match_pct']}% | {r['job_title'] or r['subject'][:50]}")
        print(f"         {r['link']}\n")

if __name__ == "__main__":
    main(days_back=90, max_results=160, only_inbox=True)
