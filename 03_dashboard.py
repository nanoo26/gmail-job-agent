import hashlib
import json
import os
import pathlib
import re
from html import escape, unescape
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="דשבורד משרות מג׳ימייל", layout="wide")

# נתיב מוחלט — עובד מכל working directory
FAVORITES_FILE = str(pathlib.Path(__file__).parent / "favorites.json")   # legacy
STATUS_FILE    = str(pathlib.Path(__file__).parent / "job_status.json")
BUSINESS_STATUS_FILE = str(pathlib.Path(__file__).parent / "job_business_status.json")
PROFILE_FILE   = str(pathlib.Path(__file__).parent / "user_profile.json")
RUN_SUMMARY_FILE = str(pathlib.Path(__file__).parent / "scan_run_summary.json")

ENABLE_PERSONALIZATION = os.getenv("PERSONALIZATION", "0") == "1"
if ENABLE_PERSONALIZATION:
    try:
        from personalization import (
            load_profile, save_profile_atomic,
            update_profile_from_job, compute_personalized_score,
        )
    except ImportError:
        ENABLE_PERSONALIZATION = False

_VALID_STATUSES = {"🆕", "👁", "⭐", "❌"}
_STATUS_LABELS = {"🆕": "חדש", "👁": "נצפה", "⭐": "מועדף", "❌": "לא מתאים"}
_STATUS_CODE_TO_EMOJI = {
    "new": "🆕",
    "viewed": "👁",
    "fav": "⭐",
    "reject": "❌",
}
_STATUS_EMOJI_TO_CODE = {v: k for k, v in _STATUS_CODE_TO_EMOJI.items()}
_STATUS_PRIORITY = {"🆕": 0, "👁": 1, "❌": 2, "⭐": 3}
_BUSINESS_VALID = {
    "fit": {"fit", "no_fit"},
    "cv_sent": {"sent", "not_sent"},
    "interview": {"yes", "no"},
}


def _empty_business_status() -> dict:
    return {"fit": "", "cv_sent": "", "interview": ""}


def set_job_status(job_id: str, new_status: str) -> None:
    """Persist a status change to session_state and to job_status.json."""
    if new_status not in _VALID_STATUSES:
        return
    st.session_state.job_status[job_id] = new_status
    with open(STATUS_FILE, "w", encoding="utf-8") as _fj:
        json.dump(st.session_state.job_status, _fj, ensure_ascii=False)
    # --- Personalization learning hook (non-critical) ---
    if ENABLE_PERSONALIZATION and new_status in {"⭐", "❌", "👁"}:
        try:
            job_row = _df_lookup_by_id(job_id)
            if job_row is not None:
                profile = load_profile(PROFILE_FILE)
                profile = update_profile_from_job(profile, job_row, new_status)
                save_profile_atomic(profile, PROFILE_FILE)
                st.session_state.pers_profile = profile
        except Exception:
            pass


def set_job_business_status(job_id: str, field: str, value: str) -> None:
    """Persist a business-status change to session_state and JSON file."""
    allowed = _BUSINESS_VALID.get(field)
    if not allowed or value not in allowed:
        return
    rec = st.session_state.job_business.get(job_id, {})
    if not isinstance(rec, dict):
        rec = _empty_business_status()
    clean = _empty_business_status()
    for f_name, f_allowed in _BUSINESS_VALID.items():
        prev = rec.get(f_name, "")
        clean[f_name] = prev if prev in f_allowed else ""
    clean[field] = value
    st.session_state.job_business[job_id] = clean
    with open(BUSINESS_STATUS_FILE, "w", encoding="utf-8") as _fj:
        json.dump(st.session_state.job_business, _fj, ensure_ascii=False)


def load_scan_run_summary(path: str = RUN_SUMMARY_FILE) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


# --- Dedup helpers ---
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "utm_id", "utm_reader", "utm_name", "utm_source_platform",
    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid",
    "ref", "referer", "source",
    "trk", "trkinfo", "trkcampaign", "trackingid",
})


def normalize_job_url(url: str) -> str:
    url = (url or "").strip()
    if not url or not url.startswith("http"):
        return ""
    try:
        p = urlparse(url)
        qs = parse_qs(p.query, keep_blank_values=True)
        qs_clean = {k: v for k, v in qs.items()
                    if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")}
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"),
                           p.params, urlencode(qs_clean, doseq=True), ""))
    except Exception:
        return url.strip()


# Patterns that indicate a URL points directly to a job posting page
DIRECT_JOB_RE = re.compile(
    r'linkedin\.com/jobs/view/\d+'
    r'|drushim\.co\.il/job/\d+'
    r'|alljobs\.co\.il.*[?&]job[iI][dD]=\d+',
    re.IGNORECASE,
)


def _normalize_alljobs_url_value(url: str) -> str:
    cleaned = unescape((url or "").strip())
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
            if key_norm == "jobid" and values:
                m = re.search(r'(\d{4,})', unescape(str(values[0])))
                if m:
                    return m.group(1)
            for raw_val in values or []:
                dec = unescape(str(raw_val))
                m_nested = re.search(r'jobid=([^&#]+)', dec, re.IGNORECASE)
                if m_nested:
                    m = re.search(r'(\d{4,})', m_nested.group(1))
                    if m:
                        return m.group(1)
    except Exception:
        pass
    m = re.search(r'[?&]jobid=([^&#]+)', cleaned, re.IGNORECASE)
    if m:
        m2 = re.search(r'(\d{4,})', m.group(1))
        if m2:
            return m2.group(1)
    return ""


def _unwrap_redirect_url(url: str) -> str:
    """Extract the real destination from common tracking/redirect URLs."""
    if not url or not url.startswith("http"):
        return url
    if "alljobs.co.il" in url.lower():
        cleaned = _normalize_alljobs_url_value(url)
        jid = _extract_alljobs_job_id(cleaned)
        if jid:
            return f"https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID={jid}"
        if "/user/mailsredirect/" in cleaned.lower() or cleaned.lower().rstrip("/") in (
            "https://www.alljobs.co.il",
            "http://www.alljobs.co.il",
        ):
            return "https://www.alljobs.co.il/User/JobsFeed/"
        return cleaned
    # LinkedIn /comm/jobs/view/ID → direct jobs/view/ID
    m = re.search(r'linkedin\.com/comm/(jobs/view/\d+)', url, re.IGNORECASE)
    if m:
        return f"https://www.linkedin.com/{m.group(1)}"
    # Generic redirect via a query param named url/target/redirect/dest
    try:
        qs = parse_qs(urlparse(url).query, keep_blank_values=True)
        for param in ("url", "target", "redirect", "dest", "destination"):
            val = qs.get(param, [""])[0]
            if val.startswith("http"):
                return val
    except Exception:
        pass
    return url


_JOB_BOARD_DOMAINS_RE = re.compile(
    r'alljobs\.co\.il|drushim\.co\.il|linkedin\.com',
    re.IGNORECASE,
)


def _is_homepage_url(url: str) -> bool:
    """Return True when url belongs to a known job board but has no numeric job identifier.

    Such URLs typically land on the site's homepage or a generic listing page
    rather than a specific job posting.
    """
    if not _JOB_BOARD_DOMAINS_RE.search(url):
        return False          # unknown domain – don't second-guess it
    if DIRECT_JOB_RE.search(url):
        return False          # matches our "direct job" pattern – fine
    try:
        p = urlparse(url)
        path_and_query = p.path.strip("/") + p.query
        # If there is no run of 4+ digits anywhere, it's almost certainly a homepage
        return not bool(re.search(r'\d{4,}', path_and_query))
    except Exception:
        return False


def resolve_best_url(job_url: str, gmail_link: str) -> tuple:
    """Return (best_url, is_direct_job_link).

    Prefers job_url; unwraps known redirects; strips tracking params.
    Falls back to the Gmail thread link when no job URL is available or
    when the URL resolves to what looks like a job-board homepage.
    """
    url = (job_url or "").strip()
    if url and url.startswith("http"):
        url = _unwrap_redirect_url(url)
        url = normalize_job_url(url)          # strip utm / trk / fbclid etc.
        if "alljobs.co.il" in url.lower() and not DIRECT_JOB_RE.search(url):
            return ("https://www.alljobs.co.il/User/JobsFeed/", False)
        if _is_homepage_url(url):
            return (gmail_link or "").strip(), False
        is_direct = bool(DIRECT_JOB_RE.search(url))
        return url, is_direct
    return (gmail_link or "").strip(), False


def build_job_id(row) -> str:
    # 1) normalized job URL — הכי יציב, מאפשר dedup
    norm = normalize_job_url(str(row.get("job_url", "")))
    if norm:
        return norm
    # 2) Gmail message ID — נצחי ויציב גם בלי job_url
    link = str(row.get("link", ""))
    if link:
        msg_id = link.rsplit("/", 1)[-1].strip()
        if msg_id:
            return "gmid:" + msg_id
    # 3) fallback אחרון: hash של נושא בלבד (יציב יותר מ-snippet)
    key = str(row.get("subject", "")).strip()
    return "hash:" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def row_anchor_id(job_id: str) -> str:
    token = hashlib.md5(str(job_id or "").encode("utf-8")).hexdigest()[:12]
    return f"jobrow-{token}"


def _extract_linkedin_job_id(url: str) -> str:
    m = re.search(r'linkedin\.com/.*/jobs/view/(\d+)', str(url or ""), re.IGNORECASE)
    return m.group(1) if m else ""


def _normalize_status_value(value: str) -> str:
    s = str(value or "").strip()
    if s in _VALID_STATUSES:
        return s
    # Defensive aliases for mojibake / legacy encodings
    return {
        "â­": "⭐",
        "ðŸ‘": "👁",
        "ðŸ†•": "🆕",
        "âŒ": "❌",
    }.get(s, "")


def _merge_status_value(prev: str, new: str) -> str:
    if new not in _VALID_STATUSES:
        return prev if prev in _VALID_STATUSES else ""
    if prev not in _VALID_STATUSES:
        return new
    return new if _STATUS_PRIORITY[new] > _STATUS_PRIORITY[prev] else prev


def migrate_status_keys_to_current_df(status: dict, df: pd.DataFrame) -> tuple[dict, bool]:
    """Map legacy status keys to current canonical job_id values."""
    if not isinstance(status, dict):
        return {}, False

    alias_to_jobid: dict[str, str] = {}
    for jid in df["job_id"].astype(str).tolist():
        alias_to_jobid[jid] = jid
        alias_to_jobid[normalize_job_url(jid)] = jid
        alljobs_id = _extract_alljobs_job_id(jid)
        if alljobs_id:
            alias_to_jobid[f"alljobs:{alljobs_id}"] = jid
            alias_to_jobid[f"https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID={alljobs_id}"] = jid
        li_id = _extract_linkedin_job_id(jid)
        if li_id:
            alias_to_jobid[f"linkedin:{li_id}"] = jid

    migrated: dict[str, str] = {}
    changed = False

    for raw_key, raw_value in status.items():
        value = _normalize_status_value(raw_value)
        if not value:
            changed = True
            continue

        raw = unescape(str(raw_key or "").strip())
        raw = raw.replace("&amp;", "&")
        norm = normalize_job_url(raw)
        alljobs_id = _extract_alljobs_job_id(raw)
        li_id = _extract_linkedin_job_id(raw)

        candidates = [
            raw,
            norm,
        ]
        if alljobs_id:
            candidates.extend([
                f"alljobs:{alljobs_id}",
                f"https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID={alljobs_id}",
            ])
        if li_id:
            candidates.append(f"linkedin:{li_id}")

        resolved_key = ""
        for cand in candidates:
            if cand and cand in alias_to_jobid:
                resolved_key = alias_to_jobid[cand]
                break
        if not resolved_key:
            resolved_key = norm or raw

        prev = migrated.get(resolved_key, "")
        merged = _merge_status_value(prev, value)
        if merged != prev:
            migrated[resolved_key] = merged

        if resolved_key != raw_key or value != raw_value:
            changed = True

    return migrated, changed


def enrich_with_dedup_info(df):
    if df.empty:
        return df.copy()
    df2 = df.copy()
    df2["occurrences_count"] = df2.groupby("job_id")["job_id"].transform("count")
    df2["source_links"] = df2.groupby("job_id")["link"].transform(
        lambda x: " | ".join(x.tolist()))
    return df2


def dedup_jobs(df):
    if df.empty:
        return df.copy(), 0, 0
    n_before = len(df)
    enriched = enrich_with_dedup_info(df)
    deduped = (enriched
               .sort_values("final_score", ascending=False)
               .drop_duplicates(subset="job_id", keep="first")
               .reset_index(drop=True))
    return deduped, n_before, len(deduped)


@st.cache_data
def load_data(path="job_emails.csv"):
    if not os.path.exists(path):
        return pd.DataFrame(), f"missing file: {path}"
    read_attempts = [
        {"encoding": "utf-8"},
        {"encoding": "utf-8-sig"},
        {"encoding": "utf-8-sig", "engine": "python", "on_bad_lines": "skip"},
    ]
    last_err = ""
    for kwargs in read_attempts:
        try:
            df = pd.read_csv(path, **kwargs)
            return df, ""
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    return pd.DataFrame(), last_err or "unknown csv read error"
    if "date" in df.columns:
        df["date_parsed"] = pd.to_datetime(df["date"], format="mixed", errors="coerce", utc=True)
    for col in ["it_score", "ops_score", "maint_score", "top_score", "cv_boost", "claude_match_pct"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    if "final_score" not in df.columns or df["final_score"].eq(0).all():
        df["final_score"] = df["top_score"]
    df["final_score"] = pd.to_numeric(df["final_score"], errors="coerce").fillna(0).astype(int)
    for col in ["best_track", "cv_recommendation", "match_reasons",
                "subject", "snippet", "from", "link", "job_url", "job_title",
                "claude_analysis", "claude_error"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    df["display_subject"] = df.apply(
        lambda r: r["job_title"] if r["job_title"].strip() else r["subject"], axis=1)
    df["job_id"] = df.apply(build_job_id, axis=1)
    return df


df, _load_data_error = load_data()

if df.empty:
    msg = "קובץ job_emails.csv ריק/לא נטען. הרץ קודם: python 02_scan_jobs.py"
    if _load_data_error:
        msg += f"\nשגיאה: {_load_data_error}"
    st.warning(msg)
    st.stop()


def _df_lookup_by_id(job_id: str) -> dict | None:
    """Return a job row as dict, or None if not found.  Uses the global df."""
    rows = df[df["job_id"] == job_id]
    if rows.empty:
        return None
    return rows.iloc[0].to_dict()


# -- Job status dict: {job_id: "🆕" | "👁" | "⭐" | "❌"} --
# Always reload from file so updates from other browser tabs are picked up automatically.
status: dict = {}

# 1) Load new-format status file if it exists
if os.path.exists(STATUS_FILE):
    with open(STATUS_FILE, "r", encoding="utf-8") as _f:
        status = json.load(_f)

# 2) Migrate legacy favorites.json (list of job_ids) → ⭐ entries
elif os.path.exists(FAVORITES_FILE):
    with open(FAVORITES_FILE, "r", encoding="utf-8") as _f:
        raw_favs: list = json.load(_f)
    link_to_jobid = dict(zip(df["link"], df["job_id"]))
    old_hash_to_jobid: dict = {}
    for _, row in df.iterrows():
        old_key_full = f"{row.get('subject', '')}|{str(row.get('snippet', ''))[:100]}"
        old_hash = "hash:" + hashlib.md5(old_key_full.encode("utf-8")).hexdigest()[:12]
        old_hash_to_jobid[old_hash] = row["job_id"]
    for key in raw_favs:
        if "mail.google.com" in key:
            new_key = link_to_jobid.get(key)
            if new_key:
                status[new_key] = "⭐"
        elif key.startswith("hash:"):
            new_key = old_hash_to_jobid.get(key, key)
            status[new_key] = "⭐"
        else:
            status[key] = "⭐"
    with open(STATUS_FILE, "w", encoding="utf-8") as _fj:
        json.dump(status, _fj, ensure_ascii=False)

# 3) Migrate legacy/redirect/misencoded status keys to current canonical job_id keys
status, _status_migrated = migrate_status_keys_to_current_df(status, df)
if _status_migrated:
    with open(STATUS_FILE, "w", encoding="utf-8") as _fj:
        json.dump(status, _fj, ensure_ascii=False)

st.session_state.job_status = status

# -- Business status dict:
# {job_id: {"fit": "fit|no_fit|", "cv_sent": "sent|not_sent|", "interview": "yes|no|"}}
business_status: dict = {}
if os.path.exists(BUSINESS_STATUS_FILE):
    try:
        with open(BUSINESS_STATUS_FILE, "r", encoding="utf-8") as _f:
            loaded_business = json.load(_f)
            if isinstance(loaded_business, dict):
                business_status = loaded_business
    except Exception:
        business_status = {}

st.session_state.job_business = business_status

_business_changed = False
for _jid in df["job_id"].tolist():
    _raw = st.session_state.job_business.get(_jid, {})
    if not isinstance(_raw, dict):
        _raw = {}
    _clean = _empty_business_status()
    for _field, _allowed in _BUSINESS_VALID.items():
        _val = _raw.get(_field, "")
        _clean[_field] = _val if _val in _allowed else ""
    if _raw != _clean:
        _business_changed = True
    st.session_state.job_business[_jid] = _clean

if _business_changed:
    with open(BUSINESS_STATUS_FILE, "w", encoding="utf-8") as _fj:
        json.dump(st.session_state.job_business, _fj, ensure_ascii=False)

# Status is mandatory: ensure every known job_id has a persisted non-empty status.
_valid_statuses = {"🆕", "👁", "⭐", "❌"}
_status_changed = False
for _jid in df["job_id"].tolist():
    _cur = st.session_state.job_status.get(_jid, "")
    if _cur not in _valid_statuses:
        st.session_state.job_status[_jid] = "🆕"
        _status_changed = True

# One-time repair for previous behavior where unknown jobs were auto-marked as seen.
_vals = [st.session_state.job_status.get(_jid, "") for _jid in df["job_id"].tolist()]
if _vals and all(v == "👁" for v in _vals):
    for _jid in df["job_id"].tolist():
        st.session_state.job_status[_jid] = "🆕"
    _status_changed = True

if _status_changed:
    with open(STATUS_FILE, "w", encoding="utf-8") as _fj:
        json.dump(st.session_state.job_status, _fj, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Personalization: load profile once per session
# ---------------------------------------------------------------------------
if ENABLE_PERSONALIZATION:
    if "pers_profile" not in st.session_state:
        st.session_state.pers_profile = load_profile(PROFILE_FILE)

    with st.sidebar:
        st.markdown("### 🧠 פרסונליזציה")
        _sig_count = st.session_state.pers_profile.get("signal_count", 0)
        st.caption(f"איתות שנלמדו: {_sig_count} (מינימום לפעילות: 3)")
        smart_sort = st.toggle("מיון חכם לפי העדפות", value=True, key="smart_sort_toggle")
        if st.button("🔄 אפס פרופיל למידה", key="reset_profile_btn"):
            from personalization import _empty_profile, save_profile_atomic as _spa
            _empty = _empty_profile()
            _spa(_empty, PROFILE_FILE)
            st.session_state.pers_profile = _empty
            st.success("הפרופיל אופס.")
            st.rerun()
else:
    smart_sort = False

def _clear_query_params() -> None:
    try:
        st.query_params.clear()
    except Exception:
        try:
            st.experimental_set_query_params()
        except Exception:
            pass


# --- Action router ---
def _qp_first(v):
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def _status_href(job_id: str, status: str, focus_token: str = "") -> str:
    params = {"set_status": str(job_id)}
    if status in _STATUS_CODE_TO_EMOJI:
        params["v_code"] = status
    else:
        params["v"] = str(status)
    if focus_token:
        params["focus"] = str(focus_token)
    return "?" + urlencode(params)


_set_jid = _qp_first(st.query_params.get("set_status", ""))
_set_val = _qp_first(st.query_params.get("v", "")).strip()
_set_code = _qp_first(st.query_params.get("v_code", "")).strip().lower()
_set_biz_jid = _qp_first(st.query_params.get("set_biz", ""))
_set_biz_field = _qp_first(st.query_params.get("bf", ""))
_set_biz_val = _qp_first(st.query_params.get("bv", ""))
_focus_row = _qp_first(st.query_params.get("focus", ""))
# Note: ?open= routing removed — job links now point directly to external URLs.

if _set_code in _STATUS_CODE_TO_EMOJI:
    _set_val = _STATUS_CODE_TO_EMOJI[_set_code]

if (
    _set_biz_jid
    and _set_biz_field in _BUSINESS_VALID
    and _set_biz_val in _BUSINESS_VALID[_set_biz_field]
):
    if _focus_row:
        st.session_state["_focus_row_anchor"] = _focus_row
    set_job_business_status(_set_biz_jid, _set_biz_field, _set_biz_val)
    _clear_query_params()
    st.rerun()

if _set_jid and _set_val in _VALID_STATUSES:
    if _focus_row:
        st.session_state["_focus_row_anchor"] = _focus_row
    set_job_status(_set_jid, _set_val)
    _clear_query_params()
    st.rerun()

# ---------------------------------------------------------------------------
# Visible UI — title, CSS, filters, KPIs, charts, tables
# ---------------------------------------------------------------------------
st.title("📬 דשבורד משרות מג׳ימייל")
st.caption("ציון סופי = top_score + cv_boost (ללא Claude) / Claude-weighted (עם Claude)  |  IT / תפעול / אחזקה  |  v2.1.0")



# -- Filters --
c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 1, 1, 1, 1])

with c1:
    senders = sorted(df["from"].dropna().unique().tolist())
    sender_filter = st.multiselect("סינון לפי מקור (From)", options=senders, default=[])

with c2:
    subject_text = st.text_input("חיפוש חופשי בכותרת/תוכן", value="")

with c3:
    track_filter = st.selectbox("מסלול", ["הכל", "IT", "תפעול", "אחזקה"])

with c4:
    min_final = st.slider("סף final_score", 0, 100, 20, 5)

with c5:
    fav_only = st.toggle("⭐ מועדפים בלבד", value=False)

with c6:
    dedup_on = st.toggle("🔄 ייחודיות", value=True,
                         help="הסר כפילויות – משרה אחת לכל מודעה")

# Apply filters
filtered = df.copy()
if sender_filter:
    filtered = filtered[filtered["from"].isin(sender_filter)]
if subject_text:
    blob = filtered["subject"] + " " + filtered["snippet"]
    filtered = filtered[blob.str.contains(subject_text, case=False, na=False)]
if track_filter != "הכל":
    filtered = filtered[filtered["best_track"] == track_filter]
filtered = filtered[filtered["final_score"] >= min_final]
if fav_only:
    filtered = filtered[filtered["job_id"].map(
        lambda jid: st.session_state.job_status.get(jid, "") == "⭐")]

# Dedup
dedup_info = None
if dedup_on:
    filtered, n_before, n_after = dedup_jobs(filtered)
    if n_before > n_after:
        dedup_info = (n_before, n_after)
else:
    filtered = enrich_with_dedup_info(filtered)
    if "occurrences_count" not in filtered.columns:
        filtered["occurrences_count"] = 1
    if "source_links" not in filtered.columns:
        filtered["source_links"] = filtered["link"]

# -- Quick status popover (choose from list, without searching by scroll) --
with st.popover("⚡ עדכון סטטוס מהרשימה"):
    if filtered.empty:
        st.caption("אין משרות זמינות לעדכון.")
    else:
        _quick_df = (
            filtered[["job_id", "display_subject", "best_track", "final_score"]]
            .drop_duplicates(subset=["job_id"], keep="first")
            .sort_values("final_score", ascending=False)
        )
        _job_options = _quick_df["job_id"].tolist()
        _job_labels = {
            row["job_id"]: f"{str(row['display_subject'])[:70]} | {row['best_track']} | ציון {int(row['final_score'])}"
            for _, row in _quick_df.iterrows()
        }
        _selected_job = st.selectbox(
            "בחר משרה",
            options=_job_options,
            format_func=lambda jid: _job_labels.get(jid, str(jid)),
            key="quick_status_job",
        )
        _current_status = st.session_state.job_status.get(_selected_job, "🆕")
        _status_options = ["🆕", "👁", "⭐", "❌"]
        _selected_status = st.selectbox(
            "בחר סטטוס",
            options=_status_options,
            index=_status_options.index(_current_status) if _current_status in _status_options else 0,
            format_func=lambda s: f"{s} {_STATUS_LABELS.get(s, '')}",
            key="quick_status_value",
        )
        if st.button("שמור סטטוס", key="quick_status_save", width="content"):
            st.session_state["_focus_row_anchor"] = row_anchor_id(_selected_job)
            set_job_status(_selected_job, _selected_status)
            st.rerun()


# ---------------------------------------------------------------------------
# Personalization scoring (runs after dedup so we score unique jobs only)
# ---------------------------------------------------------------------------
if ENABLE_PERSONALIZATION and not filtered.empty:
    _profile = st.session_state.get("pers_profile", {})
    _results = filtered.apply(
        lambda r: compute_personalized_score(r.to_dict(), _profile), axis=1
    )
    filtered["pers_delta"]   = _results.map(lambda t: t[0])
    filtered["pers_reasons"] = _results.map(lambda t: ", ".join(t[1]))

    if smart_sort and _profile.get("signal_count", 0) >= 3:
        filtered = (
            filtered
            .assign(_sort_key=lambda d: d["final_score"] + d["pers_delta"])
            .sort_values("_sort_key", ascending=False)
            .drop(columns=["_sort_key"])
            .reset_index(drop=True)
        )
else:
    if "pers_delta" not in filtered.columns:
        filtered["pers_delta"]   = 0.0
        filtered["pers_reasons"] = ""


# -- KPIs --
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("מיילים שעברו סינון", len(filtered))
k2.metric("final_score מקסימלי", int(filtered["final_score"].max()) if len(filtered) > 0 else 0)
k3.metric("ממוצע final_score",   int(filtered["final_score"].mean()) if len(filtered) > 0 else 0)
top_track = filtered["best_track"].value_counts().idxmax() if len(filtered) > 0 else "-"
k4.metric("מסלול מוביל", top_track)
_n_unseen = int(filtered["job_id"].map(
    lambda jid: st.session_state.job_status.get(jid, "🆕") == "🆕").sum())
k5.metric("לא נצפו", _n_unseen)

if dedup_info:
    st.info(
        f"🔄 Dedup: {dedup_info[0]} שורות בקלט → {dedup_info[1]} ייחודיות "
        f"(אוחדו {dedup_info[0] - dedup_info[1]} כפילויות)"
    )

st.divider()


# -- Pie + Timeline --
left, right = st.columns(2)

if len(filtered) > 0:
    track_counts = (
        filtered["best_track"].fillna("לא מסווג")
        .value_counts()
        .reset_index()
    )
    track_counts.columns = ["מסלול", "כמות"]
    fig_pie = px.pie(
        track_counts, names="מסלול", values="כמות",
        title="🎯 חלוקה לפי מסלול",
        color="מסלול",
        color_discrete_map={"IT": "#4C78A8", "תפעול": "#F58518", "אחזקה": "#54A24B"},
    )
    left.plotly_chart(fig_pie, width="stretch")

    if "date_parsed" in filtered.columns and filtered["date_parsed"].notna().any():
        timeline = (
            filtered.dropna(subset=["date_parsed"])
            .assign(day=lambda x: x["date_parsed"].dt.date)
            .groupby("day").size()
            .reset_index(name="count")
            .sort_values("day")
        )
        fig_time = px.line(timeline, x="day", y="count", markers=True,
                           title="🗓️ כמות מיילים לאורך זמן")
        fig_time.update_layout(xaxis_title="", yaxis_title="כמות")
        right.plotly_chart(fig_time, width="stretch")

st.divider()


# -- TOP 10 bar --
if len(filtered) > 0:
    top10 = filtered.nlargest(10, "final_score")[["subject", "final_score", "best_track", "cv_boost"]].copy()
    top10["subject_short"] = top10["subject"].str[:55].where(
        top10["subject"].str.len() <= 55, top10["subject"].str[:52] + "…")
    fig_top10 = px.bar(
        top10.sort_values("final_score"),
        x="final_score", y="subject_short", orientation="h",
        color="best_track", text="final_score",
        title="🔝 TOP 10 הזדמנויות לפי final_score",
        labels={"final_score": "ציון סופי", "subject_short": "משרה", "best_track": "מסלול"},
        color_discrete_map={"IT": "#4C78A8", "תפעול": "#F58518", "אחזקה": "#54A24B"},
    )
    fig_top10.update_traces(textposition="outside")
    fig_top10.update_layout(xaxis_range=[0, 110], yaxis_title="", height=420, legend_title="מסלול")
    st.plotly_chart(fig_top10, width="stretch")

st.divider()


# -- Table --
st.subheader("📋 טבלאת TOP הזדמנויות (לפי final_score)")
topn = st.slider("כמה שורות להציג", 5, 50, 15)

_is_rejected = filtered["job_id"].map(
    lambda jid: st.session_state.job_status.get(jid, "") == "❌")

show_active = (filtered[~_is_rejected]
               .sort_values("final_score", ascending=False)
               .head(topn).reset_index(drop=True).copy())

show_rejected = (filtered[_is_rejected]
                 .sort_values("final_score", ascending=False)
                 .reset_index(drop=True).copy())

_COL_CONFIG = {
    "סטטוס":             st.column_config.SelectboxColumn(
                              "סטטוס", options=["🆕", "👁", "⭐", "❌"],
                              help="🆕=חדש | 👁=נצפה | ⭐=מועדף | ❌=לא מתאים",
                              width="small"),
    "final_score":       st.column_config.NumberColumn("ציון סופי",    format="%d", width="small"),
    "cv_boost":          st.column_config.NumberColumn("בונוס קו״ח",   format="%d", width="small"),
    "best_track":        st.column_config.TextColumn("מסלול",          width="small"),
    "cv_recommendation": st.column_config.TextColumn("קו״ח לשליחה",  width="medium"),
    "match_reasons_view": st.column_config.TextColumn("מילים חופפות", width="medium"),
    "display_subject_view": st.column_config.TextColumn("משרה",        width="large"),
    "snippet_view":      st.column_config.TextColumn("תקציר",          width="large"),
    "occurrences_count": st.column_config.NumberColumn("מופעיות",   format="%d", width="small"),
    "claude_match_pct":  st.column_config.NumberColumn("Claude %",     format="%d%%", width="small"),
    "claude_analysis_view": st.column_config.TextColumn("ניתוח Claude",  width="large"),
    "link_type":         st.column_config.TextColumn("סוג קישור",    width="small"),
    "job_link":          st.column_config.LinkColumn("צפה במשרה",    width="small", display_text="פתח"),
}


def _prepare_show(df_part):
    def _trim_text(val: str, max_len: int) -> str:
        txt = str(val or "").strip()
        if len(txt) <= max_len:
            return txt
        return txt[: max_len - 1].rstrip() + "…"

    out = df_part.copy()
    _resolved = out.apply(
        lambda r: resolve_best_url(r["job_url"], r["link"]), axis=1
    )
    # job_link is the direct external URL (no ?open= routing).
    out["job_link"] = _resolved.map(lambda t: t[0])
    # link_type is informational: tells the user whether there is a direct
    # job-posting URL or only a Gmail thread link.
    out["link_type"] = _resolved.map(lambda t: "✓ ישיר" if t[1] else "📧 גמייל")
    out["display_subject_view"] = out["display_subject"].map(lambda s: _trim_text(s, 70))
    out["match_reasons_view"] = out["match_reasons"].map(lambda s: _trim_text(s, 55))
    out["snippet_view"] = out["snippet"].map(lambda s: _trim_text(s, 90))
    out["claude_analysis_view"] = out["claude_analysis"].map(lambda s: _trim_text(s, 120))
    out["סטטוס"] = (out["job_id"].map(
        lambda jid: st.session_state.job_status.get(jid, "🆕"))
        .fillna("🆕")
        .replace("None", "🆕")
        .astype(str))
    cols = ["סטטוס", "final_score", "cv_boost", "best_track",
            "cv_recommendation", "match_reasons_view", "display_subject_view", "snippet_view",
            "claude_match_pct", "claude_analysis_view",
            "link_type", "job_link"]
    if dedup_on and "occurrences_count" in out.columns:
        cols.insert(cols.index("job_link"), "occurrences_count")
    return out, [c for c in cols if c in out.columns]


def _render_accessible_preview_table(df_part, max_rows: int = 15, focus_anchor: str = ""):
    """Render a styled, high-contrast HTML table preview (read-only)."""
    if df_part.empty:
        st.info("אין נתונים להצגה בתצוגת הטבלה החדשה.")
        return

    rows = df_part.head(max_rows)
    style = """
<style>
.jobs-preview-wrap {
  border: 1px solid rgba(56, 189, 248, 0.35);
  border-radius: 12px;
  overflow: auto;
  background: linear-gradient(180deg, #0a1222 0%, #0b1324 100%);
  box-shadow: 0 8px 24px rgba(2, 6, 23, 0.45);
}
.jobs-preview {
  width: 100%;
  border-collapse: collapse;
  min-width: 1280px;
  direction: rtl;
}
.jobs-preview thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #123253;
  color: #e5f0ff;
  font-weight: 700;
  font-size: 0.88rem;
  text-align: right;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(125, 211, 252, 0.35);
}
.jobs-preview td {
  color: #e2e8f0;
  font-size: 0.86rem;
  padding: 9px 12px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
  vertical-align: top;
}
.jobs-preview tr:nth-child(even) td { background: rgba(30, 41, 59, 0.30); }
.jobs-preview tr:nth-child(odd) td { background: rgba(15, 23, 42, 0.55); }
.jobs-preview tr:hover td { background: rgba(56, 189, 248, 0.13); }
.jobs-preview .num { text-align: center; white-space: nowrap; }
.jobs-preview .job-link {
  display: inline-block;
  color: #7dd3fc;
  text-decoration: underline;
  text-underline-offset: 2px;
  font-weight: 700;
}
.jobs-preview .job-link:visited { color: #c4b5fd; }
.jobs-preview .status-form { display: inline-block; margin: 0; }
.jobs-preview .status-select {
  background: rgba(15, 23, 42, 0.92);
  color: #e2e8f0;
  border: 1px solid rgba(56, 189, 248, 0.35);
  border-radius: 8px;
  padding: 3px 8px;
  font-size: 12px;
  min-width: 98px;
  text-align: center;
  cursor: pointer;
}
.jobs-preview .status-select:focus {
  outline: none;
  border-color: rgba(125, 211, 252, 0.8);
}
.jobs-preview .status-btn {
  display: inline-block;
  background: transparent;
  cursor: pointer;
  border: 1px solid rgba(125, 211, 252, 0.25);
  border-radius: 999px;
  padding: 1px 5px;
  font-size: 0.82rem;
  color: #e2e8f0;
  line-height: 1.2;
  text-decoration: none !important;
  opacity: 0.45;
  transition: opacity 0.15s, border-color 0.15s;
}
.jobs-preview .status-btn:hover { opacity: 0.9; border-color: rgba(125, 211, 252, 0.7); }
.jobs-preview .status-btn.active { opacity: 1; border-color: rgba(125, 211, 252, 0.8); font-weight: 700; }
.jobs-preview .biz-picker { display: flex; gap: 4px; justify-content: center; }
.jobs-preview .biz-btn {
  display: inline-block;
  background: transparent;
  cursor: pointer;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 999px;
  padding: 1px 7px;
  font-size: 0.74rem;
  color: #e2e8f0;
  line-height: 1.25;
  text-decoration: none !important;
  opacity: 0.55;
  transition: opacity 0.15s, border-color 0.15s;
}
.jobs-preview .biz-btn:hover { opacity: 0.95; border-color: rgba(125, 211, 252, 0.7); }
.jobs-preview .biz-btn.active { opacity: 1; border-color: rgba(125, 211, 252, 0.9); font-weight: 700; }
.jobs-preview .view-btn {
  display: inline-block;
  background: transparent;
  cursor: pointer;
  border: 1px solid rgba(250, 204, 21, 0.45);
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 0.78rem;
  font-weight: 700;
  color: #facc15;
  text-decoration: none !important;
  white-space: nowrap;
}
.jobs-preview .view-btn:hover {
  border-color: rgba(250, 204, 21, 0.75);
  color: #fde047;
}
.jobs-preview .view-btn.done {
  border-color: rgba(250, 204, 21, 0.28);
  color: #fde68a;
  opacity: 0.9;
}
.jobs-preview .badge {
  display: inline-block;
  border: 1px solid rgba(125, 211, 252, 0.35);
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1.4;
}
.jobs-preview .new  { color: #38bdf8; border-color: rgba(56, 189, 248, 0.45); }
.jobs-preview .seen { color: #facc15; border-color: rgba(250, 204, 21, 0.45); }
.jobs-preview .fav  { color: #86efac; border-color: rgba(134, 239, 172, 0.45); }
.jobs-preview .rej  { color: #fca5a5; border-color: rgba(252, 165, 165, 0.45); }
</style>
"""

    def _status_picker(job_id: str, current: str, focus_token: str) -> str:
        options = [
            ("🆕", "חדש", "new"),
            ("👁", "נצפה", "viewed"),
            ("⭐", "מועדף", "fav"),
            ("❌", "לא מתאים", "reject"),
        ]
        if current not in {opt[0] for opt in options}:
            current = "🆕"
        current_code = _STATUS_EMOJI_TO_CODE.get(current, "new")
        items = []
        for icon, label, status_code in options:
            items.append(
                f'<option value="{escape(status_code)}"{" selected" if status_code == current_code else ""}>{escape(icon)} {escape(label)}</option>'
            )
        return (
            '<form method="get" class="status-form">'
            f'<input type="hidden" name="set_status" value="{escape(job_id)}">'
            f'<input type="hidden" name="focus" value="{escape(focus_token)}">'
            f'<select name="v_code" class="status-select" aria-label="סטטוס משרה">{"".join(items)}</select>'
            '</form>'
        )

    def _mark_viewed_btn(job_id: str, current: str, focus_token: str) -> str:
        if current == "👁":
            return '<span class="view-btn done">✓ נצפה</span>'
        return (
            '<form method="get" class="status-form">'
            f'<input type="hidden" name="set_status" value="{escape(job_id)}">'
            '<input type="hidden" name="v_code" value="viewed">'
            f'<input type="hidden" name="focus" value="{escape(focus_token)}">'
            '<button type="submit" class="view-btn" title="סמן כנצפה">👁 סמן</button>'
            '</form>'
        )

    def _business_picker(job_id: str, field: str, current: str, options, focus_token: str) -> str:
        forms = []
        for val, label, title in options:
            active_cls = " active" if current == val else ""
            forms.append(
                '<form method="get" class="status-form">'
                f'<input type="hidden" name="set_biz" value="{escape(job_id)}">'
                f'<input type="hidden" name="bf" value="{escape(field)}">'
                f'<input type="hidden" name="bv" value="{escape(val)}">'
                f'<input type="hidden" name="focus" value="{escape(focus_token)}">'
                f'<button type="submit" class="biz-btn{active_cls}" title="{escape(title)}">{escape(label)}</button>'
                '</form>'
            )
        return f'<span class="biz-picker">{"".join(forms)}</span>'

    trs = []
    _show_pers = (
        "pers_delta" in rows.columns
        and rows["pers_delta"].fillna(0).astype(float).abs().gt(0).any()
    )
    _show_claude = "claude_match_pct" in rows.columns and rows["claude_match_pct"].gt(0).any()
    for _, r in rows.iterrows():
        status_val = str(r.get("סטטוס", "🆕"))
        job_id = str(r.get("job_id", ""))
        focus_token = row_anchor_id(job_id)
        biz_rec = st.session_state.job_business.get(job_id, {})
        if not isinstance(biz_rec, dict):
            biz_rec = _empty_business_status()
        fit_val = str(biz_rec.get("fit", ""))
        cv_sent_val = str(biz_rec.get("cv_sent", ""))
        interview_val = str(biz_rec.get("interview", ""))
        fit_html = _business_picker(
            job_id,
            "fit",
            fit_val,
            [("fit", "✓ מתאים", "מתאים"), ("no_fit", "✗ לא מתאים", "לא מתאים")],
            focus_token,
        )
        cv_sent_html = _business_picker(
            job_id,
            "cv_sent",
            cv_sent_val,
            [("sent", "✓ נשלחו", "קורות חיים נשלחו"), ("not_sent", "✗ לא", "קורות חיים לא נשלחו")],
            focus_token,
        )
        interview_html = _business_picker(
            job_id,
            "interview",
            interview_val,
            [("yes", "✓ זומן", "זומן לראיון"), ("no", "✗ לא", "לא זומן לראיון")],
            focus_token,
        )
        best_url, _ = resolve_best_url(str(r.get("job_url", "")), str(r.get("link", "")))
        status_href = _status_href(job_id, "viewed", focus_token)
        # Direct link — no ?open= router; viewed-state is no longer auto-marked on open.
        link_html = (
            f'<a class="job-link js-open-and-view" href="{escape(best_url)}" target="_blank" rel="noopener" data-status-url="{escape(status_href)}">פתח משרה</a>'
            if best_url.startswith("http")
            else '<span class="badge rej">ללא לינק</span>'
        )
        _pers_cell = ""
        if _show_pers:
            _delta  = float(r.get("pers_delta", 0) or 0)
            _reason = str(r.get("pers_reasons", "") or "")
            if _delta > 0:
                _pers_cell = f'<td class="num" title="{escape(_reason)}" style="color:#86efac">+{_delta:.0f}</td>'
            elif _delta < 0:
                _pers_cell = f'<td class="num" title="{escape(_reason)}" style="color:#fca5a5">{_delta:.0f}</td>'
            else:
                _pers_cell = f'<td class="num" title="{escape(_reason)}">—</td>'
        _claude_cell = ""
        if _show_claude:
            _cpct = int(r.get("claude_match_pct", 0) or 0)
            _analysis = str(r.get("claude_analysis", "") or "")[:200]
            _color = "#86efac" if _cpct >= 70 else ("#facc15" if _cpct >= 45 else "#fca5a5")
            _claude_cell = (
                f'<td class="num" title="{escape(_analysis)}" style="color:{_color}">{_cpct}%</td>'
                if _cpct > 0 else '<td class="num">—</td>'
            )
        trs.append(
            f'<tr id="{escape(focus_token)}">'
            f'<td class="num">{_status_picker(job_id, status_val, focus_token)}</td>'
            f'<td class="num">{_mark_viewed_btn(job_id, status_val, focus_token)}</td>'
            f'<td class="num">{fit_html}</td>'
            f'<td class="num">{cv_sent_html}</td>'
            f'<td class="num">{interview_html}</td>'
            f'<td class="num">{int(r.get("final_score", 0))}</td>'
            + (_pers_cell if _show_pers else "")
            + (_claude_cell if _show_claude else "")
            + f'<td>{escape(str(r.get("best_track", "")))}</td>'
            f'<td>{escape(str(r.get("display_subject_view", "")))}</td>'
            f'<td>{escape(str(r.get("snippet_view", "")))}</td>'
            f'<td class="num">{escape(str(r.get("link_type", "")))}</td>'
            f'<td class="num">{link_html}</td>'
            "</tr>"
        )

    _pers_th = "<th scope='col'>ציון אישי</th>" if _show_pers else ""
    _claude_th = "<th scope='col'>Claude %</th>" if _show_claude else ""
    table_html = (
        '<div class="jobs-preview-wrap">'
        '<table class="jobs-preview" role="table" aria-label="תצוגת משרות נגישה">'
        "<thead><tr>"
        "<th scope='col'>סטטוס</th>"
        "<th scope='col'>נצפה</th>"
        "<th scope='col'>התאמה</th>"
        "<th scope='col'>קו\"ח</th>"
        "<th scope='col'>ראיון</th>"
        "<th scope='col'>ציון</th>"
        + _pers_th
        + _claude_th
        + "<th scope='col'>מסלול</th>"
        "<th scope='col'>משרה</th>"
        "<th scope='col'>תקציר</th>"
        "<th scope='col'>סוג</th>"
        "<th scope='col'>קישור</th>"
        "</tr></thead>"
        f"<tbody>{''.join(trs)}</tbody>"
        "</table></div>"
    )

    st.markdown(style + table_html, unsafe_allow_html=True)
    if focus_anchor:
        st.components.v1.html(
            f"""
<script>
(() => {{
  const anchor = {json.dumps(focus_anchor)};
  if (!anchor) return;
  function scrollToAnchor() {{
    const doc = (window.parent && window.parent.document) ? window.parent.document : document;
    const el = doc.getElementById(anchor);
    if (el) {{
      el.scrollIntoView({{ behavior: "auto", block: "center" }});
    }}
  }}
  setTimeout(scrollToAnchor, 0);
  setTimeout(scrollToAnchor, 120);
  setTimeout(scrollToAnchor, 360);
}})();
</script>
""",
            height=0,
        )


# Active jobs table
show_a, dcols_a = _prepare_show(show_active)
_pending_focus_anchor = str(st.session_state.pop("_focus_row_anchor", "") or "").strip()

st.markdown("#### תצוגה חדשה ונגישה")
_render_accessible_preview_table(show_a, max_rows=topn, focus_anchor=_pending_focus_anchor)

# Sources expander for multi-source deduplicated jobs (active only)
if dedup_on and "source_links" in show_a.columns:
    multi = show_a[show_a["occurrences_count"] > 1]
    if len(multi) > 0:
        with st.expander(
            f"🔗 מקורות גמייל למשרות כפולות ({len(multi)} משרות)", expanded=False
        ):
            for _, row in multi.iterrows():
                subj = str(row["subject"])[:70]
                links = str(row["source_links"]).split(" | ")
                st.markdown(f"**{subj}** — {len(links)} מיילים")
                for lnk in links:
                    lnk = lnk.strip()
                    if lnk:
                        st.markdown(f"  - [{lnk[:80]}]({lnk})")

# Rejected jobs section
if len(show_rejected) > 0:
    st.divider()
    st.markdown(f"#### ❌ משרות שסומנו כלא מתאים ({len(show_rejected)} משרות)")
    show_r, dcols_r = _prepare_show(show_rejected)
    _render_accessible_preview_table(show_r, max_rows=max(10, topn), focus_anchor=_pending_focus_anchor)

st.download_button(
    "⬇️ הורד CSV מסונן",
    data=filtered.to_csv(index=False).encode("utf-8-sig"),
    file_name="job_emails_filtered.csv",
    mime="text/csv",
)

st.divider()
st.subheader("דוח Claude מההרצה האחרונה")

run_summary = load_scan_run_summary()
if not run_summary:
    st.info("אין דוח הרצה זמין עדיין")
else:
    run_ts = str(run_summary.get("run_timestamp", "")).strip()
    if run_ts:
        st.caption(f"זמן הרצה: {run_ts}")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("קריאות Claude", int(run_summary.get("claude_calls_attempted", 0) or 0))
    m2.metric("הצלחות", int(run_summary.get("claude_success_count", 0) or 0))
    m3.metric("שגיאות", int(run_summary.get("claude_error_count", 0) or 0))
    m4.metric("התאמות חיוביות", int(run_summary.get("claude_positive_count", 0) or 0))
    avg_pct = run_summary.get("claude_avg_match_pct", 0)
    max_pct = int(run_summary.get("claude_max_match_pct", 0) or 0)
    m5.metric("ממוצע / מקסימום", f"{avg_pct}% / {max_pct}%")

    d1, d2, d3 = st.columns(3)
    d1.metric("התאמות Claude ב-CSV", int(run_summary.get("dataset_positive_count", 0) or 0))
    d2.metric("ממוצע ב-CSV", f"{run_summary.get('dataset_avg_match_pct', 0)}%")
    d3.metric("מקסימום ב-CSV", f"{int(run_summary.get('dataset_max_match_pct', 0) or 0)}%")

    top_rows = run_summary.get("top_5_claude_rows", [])
    if not top_rows:
        # Backward-compatible fallback when old summary files lack top rows.
        _tmp = df.copy()
        _tmp["_cm"] = pd.to_numeric(_tmp.get("claude_match_pct", 0), errors="coerce").fillna(0).astype(int)
        _tmp = _tmp.sort_values(["_cm", "final_score"], ascending=[False, False]).drop_duplicates(
            subset=["job_id"], keep="first"
        )
        top_rows = []
        for _, rr in _tmp.head(5).iterrows():
            top_rows.append(
                {
                    "title": str(rr.get("job_title") or rr.get("subject", ""))[:180],
                    "match_pct": int(rr.get("_cm", 0) or 0),
                    "track": str(rr.get("claude_cv_track") or rr.get("best_track", "")),
                    "error": str(rr.get("claude_error", "") or "")[:120],
                }
            )

    if isinstance(top_rows, list) and top_rows:
        top_df = pd.DataFrame(top_rows)
        col_order = [c for c in ["title", "match_pct", "track", "error"] if c in top_df.columns]
        if col_order:
            top_df = top_df[col_order].rename(
                columns={
                    "title": "משרה",
                    "match_pct": "% התאמה",
                    "track": "מסלול",
                    "error": "שגיאה",
                }
            )
        st.dataframe(top_df, use_container_width=True, hide_index=True)
    else:
        st.caption("אין שורות Claude להצגה בדוח האחרון")

# ---------------------------------------------------------------------------
# JS block: CSS injection for ag-Grid theme.
#
# CSS IIFE: retries every 250ms for up to 7.5s, injects into
# window.parent.document.head, uses data-version for idempotency.
# ---------------------------------------------------------------------------
st.components.v1.html(
    """
<script>
// ── CSS injection (robust retry-based IIFE) ────────────────────────────────
(() => {
  const STYLE_ID = "job-agent-aggrid-theme";
  const CSS_VERSION = "v2.0.0";
  const MAX_TRIES = 30;
  const INTERVAL_MS = 250;

  const CSS_TEXT = `
/* Job Agent ag-Grid theme (v2.0.0) */

.ag-root-wrapper,
.ag-root-wrapper-body {
  border-radius: 12px !important;
}

.ag-root-wrapper {
  overflow: hidden !important;
  border: 1px solid rgba(148, 163, 184, 0.28) !important;
  box-shadow: 0 14px 30px rgba(2, 6, 23, 0.45), 0 2px 10px rgba(15, 23, 42, 0.35) !important;
  background: #070d1a !important;
}

.ag-header {
  background: linear-gradient(90deg, #0b1324 0%, #12304b 52%, #0f2e4d 100%) !important;
  border-bottom: 1px solid rgba(125, 211, 252, 0.28) !important;
}

.ag-header-viewport,
.ag-header-container,
.ag-header-row {
  background: transparent !important;
}

.ag-header-cell,
.ag-header-group-cell {
  background: transparent !important;
  border-right: 1px solid rgba(148, 163, 184, 0.18) !important;
}

.ag-header-cell-label,
.ag-header-group-cell-label {
  color: #e2e8f0 !important;
  font-weight: 700 !important;
  letter-spacing: 0.15px !important;
}

.ag-body,
.ag-body-viewport,
.ag-center-cols-viewport,
.ag-center-cols-container,
.ag-floating-top-viewport,
.ag-floating-bottom-viewport {
  background: #070d1a !important;
}

.ag-row {
  border-bottom: 1px solid rgba(148, 163, 184, 0.12) !important;
}

.ag-center-cols-container .ag-row:nth-child(even) .ag-cell,
.ag-pinned-left-cols-container .ag-row:nth-child(even) .ag-cell,
.ag-pinned-right-cols-container .ag-row:nth-child(even) .ag-cell {
  background: rgba(15, 23, 42, 0.78) !important;
}

.ag-center-cols-container .ag-row:nth-child(odd) .ag-cell,
.ag-pinned-left-cols-container .ag-row:nth-child(odd) .ag-cell,
.ag-pinned-right-cols-container .ag-row:nth-child(odd) .ag-cell {
  background: rgba(30, 41, 59, 0.38) !important;
}

.ag-cell,
.ag-full-width-row .ag-cell-wrapper,
.ag-group-value {
  color: rgba(241, 245, 249, 0.95) !important;
  border-right: 1px solid rgba(148, 163, 184, 0.14) !important;
}

.ag-row-hover .ag-cell,
.ag-row:hover .ag-cell {
  background: rgba(56, 189, 248, 0.12) !important;
}

.ag-row-selected .ag-cell {
  background: rgba(14, 165, 233, 0.2) !important;
}

.ag-cell-focus,
.ag-cell:focus-within {
  outline: 2px solid rgba(56, 189, 248, 0.75) !important;
  outline-offset: -2px !important;
}

.ag-header-icon,
.ag-icon {
  color: rgba(226, 232, 240, 0.92) !important;
  fill: rgba(226, 232, 240, 0.92) !important;
}

.ag-cell a[href],
[data-testid="stDataEditor"] a[href] {
  color: #38bdf8 !important;
  text-decoration: underline !important;
  text-decoration-thickness: 1px !important;
  text-underline-offset: 2px !important;
  font-weight: 600 !important;
}

.ag-cell a[href]:hover,
[data-testid="stDataEditor"] a[href]:hover {
  color: #67e8f9 !important;
}

.ag-cell a[href]:visited,
[data-testid="stDataEditor"] a[href]:visited {
  color: #a78bfa !important;
}

.ag-body-viewport::-webkit-scrollbar,
.ag-center-cols-viewport::-webkit-scrollbar,
.ag-body-horizontal-scroll-viewport::-webkit-scrollbar {
  height: 10px !important;
  width: 10px !important;
}

.ag-body-viewport::-webkit-scrollbar-track,
.ag-center-cols-viewport::-webkit-scrollbar-track,
.ag-body-horizontal-scroll-viewport::-webkit-scrollbar-track {
  background: rgba(148, 163, 184, 0.12) !important;
  border-radius: 10px !important;
}

.ag-body-viewport::-webkit-scrollbar-thumb,
.ag-center-cols-viewport::-webkit-scrollbar-thumb,
.ag-body-horizontal-scroll-viewport::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(14, 165, 233, 0.95), rgba(37, 99, 235, 0.85)) !important;
  border-radius: 10px !important;
  border: 2px solid rgba(7, 13, 26, 0.9) !important;
}

.ag-body-viewport,
.ag-center-cols-viewport,
.ag-body-horizontal-scroll-viewport {
  scrollbar-width: thin !important;
  scrollbar-color: rgba(56, 189, 248, 0.8) rgba(148, 163, 184, 0.12) !important;
}

.ag-ltr .ag-cell,
.ag-rtl .ag-cell {
  border-color: rgba(148, 163, 184, 0.14) !important;
}
`;

  function getTargetDocument() {
    try {
      if (window.parent && window.parent !== window && window.parent.document)
        return window.parent.document;
    } catch (e) {}
    return document;
  }

  function tryInject(attempt) {
    const doc = getTargetDocument();
    const head = doc && doc.head ? doc.head : null;
    if (!head) {
      if (attempt >= MAX_TRIES) {
        console.error("[JobAgent] CSS injection failed: head not ready after " + MAX_TRIES + " tries.");
        return true;
      }
      return false;
    }
    try {
      const existing = doc.getElementById(STYLE_ID);
      if (existing) {
        if (existing.getAttribute("data-version") === CSS_VERSION) {
          console.log("[JobAgent] CSS already loaded (version=" + CSS_VERSION + ").");
          return true;
        }
        existing.textContent = CSS_TEXT;
        existing.setAttribute("data-version", CSS_VERSION);
        console.log("[JobAgent] CSS updated successfully (version=" + CSS_VERSION + ").");
        return true;
      }
      const styleEl = doc.createElement("style");
      styleEl.id = STYLE_ID;
      styleEl.type = "text/css";
      styleEl.setAttribute("data-version", CSS_VERSION);
      styleEl.appendChild(doc.createTextNode(CSS_TEXT));
      head.appendChild(styleEl);
      console.log("[JobAgent] CSS injected successfully (version=" + CSS_VERSION + ").");
      return true;
    } catch (e) {
      console.error("[JobAgent] CSS injection error:", e);
      return true;
    }
  }

  let tries = 0;
  const timer = setInterval(() => {
    tries += 1;
    const done = tryInject(tries);
    if (done || tries >= MAX_TRIES) clearInterval(timer);
  }, INTERVAL_MS);
})();

// Keep table position after status updates:
// save current scroll before click/submit, restore after rerun.
(() => {
  const KEY = "job-agent-scroll-y";

  function getTargetWindow() {
    try {
      if (window.parent && window.parent !== window) return window.parent;
    } catch (e) {}
    return window;
  }

  function getTargetDocument() {
    try {
      const w = getTargetWindow();
      if (w && w.document) return w.document;
    } catch (e) {}
    return document;
  }

  function saveScroll() {
    try {
      const w = getTargetWindow();
      const y = Math.max(w.scrollY || 0, w.pageYOffset || 0);
      w.sessionStorage.setItem(KEY, String(y));
    } catch (e) {}
  }

  function restoreScroll() {
    try {
      const w = getTargetWindow();
      const raw = w.sessionStorage.getItem(KEY);
      if (!raw) return;
      const y = parseInt(raw, 10);
      w.sessionStorage.removeItem(KEY);
      if (Number.isNaN(y)) return;
      const jump = () => {
        w.scrollTo(0, y);
        setTimeout(() => w.scrollTo(0, y), 80);
        setTimeout(() => w.scrollTo(0, y), 260);
      };
      jump();
    } catch (e) {}
  }

  function bindHandlers() {
    const doc = getTargetDocument();

    doc.querySelectorAll(".jobs-preview a.js-open-and-view").forEach((el) => {
      if (el.dataset.viewSyncBound === "1") return;
      el.dataset.viewSyncBound = "1";

      const markViewedUi = () => {
        const row = el.closest("tr");
        if (!row) return;
        const summary = row.querySelector(".status-summary");
        if (summary) summary.textContent = "👁 נצפה";
        row.querySelectorAll(".status-item").forEach((item) => {
          const statusVal = (item.getAttribute("data-status-val") || "").trim();
          if (statusVal === "👁") item.classList.add("active");
          else item.classList.remove("active");
        });
        const viewCell = row.querySelector(".view-btn");
        if (viewCell && viewCell.tagName && viewCell.tagName.toLowerCase() === "a") {
          const done = doc.createElement("span");
          done.className = "view-btn done";
          done.textContent = "✓ נצפה";
          viewCell.replaceWith(done);
        }
      };

      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        saveScroll();
        const w = getTargetWindow();
        const openUrl = (el.getAttribute("href") || "").trim();
        const statusUrl = (el.getAttribute("data-status-url") || "").trim();

        // 1. Open job in new tab
        if (openUrl && /^https?:\\/\\//i.test(openUrl)) {
          try {
            const opened = window.open(openUrl, "_blank", "noopener");
            if (opened) {
              try { opened.opener = null; } catch (e) {}
            }
          } catch (e) {}
        }

        // 2. Update UI immediately
        markViewedUi();

        // 3. Navigate current tab to status URL — query string only,
        //    avoids wrong origin behavior from iframe context.
        if (statusUrl) {
          try {
            let qs = statusUrl;
            if (statusUrl.startsWith("http") || statusUrl.startsWith("//")) {
              qs = new URL(statusUrl, w.location.href).search;
            } else if (statusUrl.startsWith("?")) {
              qs = statusUrl;
            }
            const base = w.location.href.split("?")[0].split("#")[0];
            w.location.assign(base + qs);
          } catch (e) {
            try { w.location.assign(statusUrl); } catch (e2) {}
          }
        }
      }, { capture: true });
    });

    doc.querySelectorAll(".jobs-preview a.status-item, .jobs-preview a.view-btn:not(.done)").forEach((el) => {
      if (el.dataset.sameTabBound === "1") return;
      el.dataset.sameTabBound = "1";
      el.addEventListener("click", (ev) => {
        const href = (el.getAttribute("href") || "").trim();
        if (!href) return;
        const w = getTargetWindow();
        if (el.classList.contains("js-open-and-view")) return;
        ev.preventDefault();
        saveScroll();
        try {
          const nextUrl = new URL(href, w.location.href).toString();
          w.location.assign(nextUrl);
        } catch (e) {
          try { w.location.assign(href); } catch (e2) {}
        }
      }, { capture: true });
    });

    doc.querySelectorAll(".jobs-preview .status-select").forEach((sel) => {
      if (sel.dataset.statusSelectBound === "1") return;
      sel.dataset.statusSelectBound = "1";
      sel.addEventListener("change", () => {
        saveScroll();
        const form = sel.closest("form");
        if (!form) return;
        try { form.submit(); } catch (e) {}
      });
      sel.addEventListener("pointerdown", saveScroll, { capture: true });
      sel.addEventListener("touchstart", saveScroll, { capture: true, passive: true });
    });

    const selector = [
      "a.status-item",
      ".jobs-preview .view-btn",
      ".jobs-preview .biz-btn",
      ".jobs-preview .status-btn",
      ".jobs-preview a.job-link"
    ].join(",");

    doc.querySelectorAll(selector).forEach((el) => {
      if (el.dataset.scrollBound === "1") return;
      el.dataset.scrollBound = "1";
      el.addEventListener("click", saveScroll, { capture: true });
      el.addEventListener("pointerdown", saveScroll, { capture: true });
      el.addEventListener("touchstart", saveScroll, { capture: true, passive: true });
    });

    doc.querySelectorAll(".jobs-preview form").forEach((form) => {
      if (form.dataset.scrollBound === "1") return;
      form.dataset.scrollBound = "1";
      form.addEventListener("submit", saveScroll, { capture: true });
    });
  }

  restoreScroll();
  bindHandlers();

  const doc = getTargetDocument();
  const root = doc.body || doc.documentElement;
  if (!root) return;
  const observer = new MutationObserver(() => bindHandlers());
  observer.observe(root, { childList: true, subtree: true });
  setTimeout(() => observer.disconnect(), 15000);
})();
</script>
""",
    height=0,
)
