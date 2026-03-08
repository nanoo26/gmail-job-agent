"""
personalization.py — ML-lite personalization layer for the Gmail Job Agent.

Learns from explicit user signals (⭐ / ❌ / 👁) and adjusts per-job scores
via a weighted online model.  Designed to be imported by 03_dashboard.py.

Feature flag: set env var  PERSONALIZATION=1  to activate.
"""

from __future__ import annotations

import json
import os
import re
import pathlib
import tempfile
from typing import Any

# ---------------------------------------------------------------------------
# Hyper-parameters
# ---------------------------------------------------------------------------
PROFILE_VERSION     = 1
LIKED_DELTA         =  0.15   # ⭐ → raise weights
DISLIKED_DELTA      = -0.20   # ❌ → lower weights
VIEWED_DELTA        =  0.02   # 👁 → slight positive (optional)
MAX_WEIGHT          =  1.0    # clamp range  [-1, +1]
DECAY               =  0.98   # applied to all weights on every profile save
MIN_SIGNALS         =  3      # minimum total signals before delta is shown in UI
DELTA_SCALE         =  15.0   # raw weight → score-point multiplier
BLACKLIST_THRESHOLD = -0.40   # keyword weight below this → soft blacklist
LEARN_FROM_VIEWED   = False   # set True to learn from 👁 as well


# ---------------------------------------------------------------------------
# Profile schema helpers
# ---------------------------------------------------------------------------

def _empty_profile() -> dict:
    return {
        "version":           PROFILE_VERSION,
        "signal_count":      0,
        "track_weights":     {},   # {"IT": 0.3, "תפעול": -0.1, "אחזקה": 0.0}
        "keyword_weights":   {},   # {"helpdesk": 0.5, "sysadmin": 0.3, ...}
        "sender_weights":    {},   # {"linkedin.com": 0.1, ...}
        "soft_blacklist":    [],   # keywords whose weight < BLACKLIST_THRESHOLD
    }


def _migrate(profile: dict) -> dict:
    """Forward-migrate old profile schema versions."""
    base = _empty_profile()
    base.update(profile)
    base["version"] = PROFILE_VERSION
    return base


def load_profile(path: str) -> dict:
    """Load profile from *path*; return empty profile on any error."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                return _migrate(raw)
    except Exception:
        pass
    return _empty_profile()


def save_profile_atomic(profile: dict, path: str) -> None:
    """Write profile to *path* atomically (temp-file + os.replace)."""
    dir_ = str(pathlib.Path(path).parent)
    try:
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(profile, fh, ensure_ascii=False, indent=2)
        except Exception:
            os.close(fd)
            raise
        os.replace(tmp, path)
    except Exception:
        # Non-critical — silently swallow so dashboard never crashes
        pass


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-zA-Z\u05d0-\u05ea]{3,}")


def _extract_features(job: dict) -> dict:
    """
    Extract a flat feature dict from a job row (dict with keys from the df).

    Returns:
        {
            "track":    str,          # best_track value
            "sender":   str,          # domain extracted from 'from'
            "keywords": list[str],    # tokens from subject + job_title + snippet
        }
    """
    track  = str(job.get("best_track", "") or "").strip()
    sender_raw = str(job.get("from", "") or "")
    # Extract domain: take the part after @ if present
    m = re.search(r"@([\w.\-]+)", sender_raw)
    sender = m.group(1).lower() if m else sender_raw.lower()[:40]

    blob = " ".join([
        str(job.get("subject",   "") or ""),
        str(job.get("job_title", "") or ""),
        str(job.get("snippet",   "") or ""),
    ]).lower()
    keywords = list({w for w in _WORD_RE.findall(blob) if 3 <= len(w) <= 30})

    return {"track": track, "sender": sender, "keywords": keywords}


# ---------------------------------------------------------------------------
# Core learning / scoring
# ---------------------------------------------------------------------------

def _clamp(val: float) -> float:
    return max(-MAX_WEIGHT, min(MAX_WEIGHT, val))


def _apply_decay(profile: dict) -> dict:
    """Multiply every weight by DECAY (called on every profile update)."""
    for store in ("track_weights", "keyword_weights", "sender_weights"):
        profile[store] = {k: _clamp(v * DECAY) for k, v in profile[store].items()}
    return profile


def _rebuild_blacklist(profile: dict) -> dict:
    profile["soft_blacklist"] = [
        kw for kw, w in profile["keyword_weights"].items()
        if w <= BLACKLIST_THRESHOLD
    ]
    return profile


def update_profile_from_job(profile: dict, job: dict, action: str) -> dict:
    """
    Return an *updated copy* of *profile* after learning from *action*.

    action: one of  "⭐"  "❌"  "👁"
    """
    if action == "⭐":
        delta = LIKED_DELTA
    elif action == "❌":
        delta = DISLIKED_DELTA
    elif action == "👁" and LEARN_FROM_VIEWED:
        delta = VIEWED_DELTA
    else:
        return profile   # nothing to learn

    p = json.loads(json.dumps(profile))   # deep copy
    _apply_decay(p)
    p["signal_count"] = p.get("signal_count", 0) + 1

    feats = _extract_features(job)

    # Track weight
    t = feats["track"]
    if t:
        p["track_weights"][t] = _clamp(p["track_weights"].get(t, 0.0) + delta)

    # Sender weight
    s = feats["sender"]
    if s:
        p["sender_weights"][s] = _clamp(p["sender_weights"].get(s, 0.0) + delta)

    # Keyword weights (only top-N to keep profile compact)
    for kw in feats["keywords"][:40]:
        p["keyword_weights"][kw] = _clamp(p["keyword_weights"].get(kw, 0.0) + delta)

    _rebuild_blacklist(p)
    return p


def compute_personalized_score(job: dict, profile: dict) -> tuple[float, list[str]]:
    """
    Compute ``(delta, reasons)`` for *job* given the current *profile*.

    *delta* is an additive adjustment to ``final_score`` (capped internally
    to keep the total in a reasonable range).  *reasons* is a list of
    human-readable strings explaining the largest contributing signals.

    Returns ``(0.0, [])`` when there aren't enough signals yet.
    """
    if profile.get("signal_count", 0) < MIN_SIGNALS:
        return 0.0, []

    feats   = _extract_features(job)
    contrib: dict[str, float] = {}

    # Track
    t = feats["track"]
    if t and t in profile["track_weights"]:
        contrib[f"מסלול:{t}"] = profile["track_weights"][t]

    # Sender
    s = feats["sender"]
    if s and s in profile["sender_weights"]:
        contrib[f"מקור:{s}"] = profile["sender_weights"][s]

    # Keywords
    for kw in feats["keywords"]:
        w = profile["keyword_weights"].get(kw, 0.0)
        if abs(w) >= 0.05:
            contrib[kw] = w

    if not contrib:
        return 0.0, []

    raw_delta = sum(contrib.values())
    delta     = max(-20.0, min(20.0, raw_delta * DELTA_SCALE))

    # Top-3 reasons by absolute contribution
    top3 = sorted(contrib.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
    reasons = []
    for name, w in top3:
        sign = "+" if w > 0 else ""
        reasons.append(f"{name} {sign}{w:.2f}")

    return round(delta, 1), reasons
