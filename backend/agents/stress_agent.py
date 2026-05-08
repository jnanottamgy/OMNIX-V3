"""
OMNIX V3 — Stress & Emotion Detection Agent
Reads multiple stress signals and produces a unified stress profile:
  - Calendar density (meeting overload)
  - Email inbox load
  - Sleep deficit
  - Typing speed signal (from frontend heartbeat)
  - Activity gap (no exercise in N days)
  - Time-of-day cortisol curve

Returns a StressProfile used by Planner to adjust schedule.
"""
from datetime import datetime


def analyse(context: dict, gmail_data: dict, calendar_data: dict, user_input: str = "") -> dict:
    """
    Aggregates all stress signals into a unified StressProfile.
    Returns score 0-100, level, and per-signal breakdown.
    """
    signals = {}
    score = 0

    # ── Signal 1: Sleep deficit ─────────────────────────────────────────────
    sleep_score = context.get("sleep_score", 70)
    sleep_deficit = max(0, 70 - sleep_score)
    sleep_contrib = min(sleep_deficit * 0.5, 25)
    signals["sleep_deficit"] = {
        "value": sleep_score,
        "contribution": round(sleep_contrib),
        "label": f"Sleep score {sleep_score}/100"
    }
    score += sleep_contrib

    # ── Signal 2: Calendar density ───────────────────────────────────────────
    event_count = calendar_data.get("event_count", 0)
    density = calendar_data.get("density_score", 0)
    cal_contrib = min(density * 0.2, 20)
    signals["calendar_density"] = {
        "value": event_count,
        "contribution": round(cal_contrib),
        "label": f"{event_count} events today"
    }
    score += cal_contrib

    # ── Signal 3: Email overload ─────────────────────────────────────────────
    unread = gmail_data.get("unread_count", 0)
    email_contrib = min(unread * 0.4, 20)
    signals["email_overload"] = {
        "value": unread,
        "contribution": round(email_contrib),
        "label": f"{unread} unread emails"
    }
    score += email_contrib

    # ── Signal 4: Cortisol curve (time of day) ───────────────────────────────
    hour = datetime.now().hour
    # Cortisol naturally peaks 8-9am, dips 2-3pm, rises again 6pm
    if 6 <= hour <= 9:
        cortisol_contrib = 5   # naturally elevated — manageable
    elif 13 <= hour <= 15:
        cortisol_contrib = 8   # post-lunch dip = stress felt more acutely
    elif 22 <= hour or hour <= 2:
        cortisol_contrib = 12  # late night = elevated cortisol + poor decisions
    else:
        cortisol_contrib = 3
    signals["cortisol_curve"] = {
        "value": hour,
        "contribution": cortisol_contrib,
        "label": f"Time of day ({hour:02d}:00)"
    }
    score += cortisol_contrib

    # ── Signal 5: No breakfast ───────────────────────────────────────────────
    if not context.get("breakfast_consumed", True):
        signals["no_breakfast"] = {
            "value": False,
            "contribution": 8,
            "label": "Skipped breakfast"
        }
        score += 8

    # ── Signal 6: Stress keywords in user input ──────────────────────────────
    stress_words = ["stressed", "anxious", "overwhelmed", "panic", "deadline",
                    "exhausted", "burnt out", "can't focus", "too much"]
    keyword_hits = sum(1 for w in stress_words if w in user_input.lower())
    keyword_contrib = min(keyword_hits * 4, 15)
    if keyword_hits > 0:
        signals["user_language"] = {
            "value": keyword_hits,
            "contribution": keyword_contrib,
            "label": f"{keyword_hits} stress keyword(s) in input"
        }
        score += keyword_contrib

    # ── Signal 7: Priority emails ────────────────────────────────────────────
    priority_count = len(gmail_data.get("priority_emails", []))
    if priority_count > 0:
        prio_contrib = min(priority_count * 5, 10)
        signals["priority_emails"] = {
            "value": priority_count,
            "contribution": prio_contrib,
            "label": f"{priority_count} high-priority email(s)"
        }
        score += prio_contrib

    # ── Final score ──────────────────────────────────────────────────────────
    score = min(round(score), 100)

    if score >= 70:
        level = "CRITICAL"
        recommendation = "Immediate intervention needed. OMNIX will simplify your schedule, batch tasks, and protect recovery windows."
    elif score >= 45:
        level = "HIGH"
        recommendation = "Elevated stress detected. OMNIX will add buffer time, cut non-essential tasks, and prioritise recovery."
    elif score >= 25:
        level = "MEDIUM"
        recommendation = "Moderate stress. OMNIX will pace your schedule and flag if it increases."
    else:
        level = "LOW"
        recommendation = "You're in a good state. OMNIX will hold this baseline."

    return {
        "stress_score":    score,
        "stress_level":    level,
        "recommendation":  recommendation,
        "signals":         signals,
        "analysed_at":     datetime.now().isoformat()
    }
