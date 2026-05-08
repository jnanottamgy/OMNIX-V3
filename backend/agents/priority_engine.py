"""
OMNIX V3 — Priority Score Engine
Scores every incoming notification, email, and task.
Surfaces only what genuinely matters based on:
  - Sender relationship strength (frequency in memory)
  - Subject urgency keywords
  - Time sensitivity (deadline proximity)
  - User's historical response patterns
  - Calendar context (exam day = lower notification threshold)

50 notifications in → 3 surfaced.
"""
from datetime import datetime


# ── Urgency keyword banks ─────────────────────────────────────────────────────
CRITICAL_KEYWORDS = [
    "urgent", "asap", "immediately", "emergency", "critical", "deadline",
    "exam", "test result", "interview", "offer letter", "action required",
    "payment", "overdue", "final notice", "expiring", "last chance"
]
HIGH_KEYWORDS = [
    "meeting", "reminder", "due today", "due tomorrow", "follow up",
    "reply needed", "rsvp", "confirm", "submit", "presentation"
]
LOW_KEYWORDS = [
    "newsletter", "promo", "sale", "unsubscribe", "no-reply",
    "notification", "digest", "weekly", "monthly", "update"
]


def score_item(item: dict, memory: dict, context: dict) -> dict:
    """
    Score a single notification/email/task.
    Returns the item with priority_score (0-100) and surface (bool).
    """
    score = 50  # baseline

    source_text = (
        (item.get("title", "") + " " +
         item.get("subject", "") + " " +
         item.get("body", "") + " " +
         item.get("from", "")).lower()
    )

    # ── Keyword scoring ───────────────────────────────────────────────────────
    for kw in CRITICAL_KEYWORDS:
        if kw in source_text:
            score += 20
            break

    for kw in HIGH_KEYWORDS:
        if kw in source_text:
            score += 10
            break

    for kw in LOW_KEYWORDS:
        if kw in source_text:
            score -= 25
            break

    # ── Sender relationship ───────────────────────────────────────────────────
    sender = item.get("from", "").lower()
    known_senders = memory.get("personalization", {}).get("known_senders", {})
    if sender in known_senders:
        freq = known_senders[sender]
        score += min(freq * 2, 15)  # frequently communicates = higher priority

    # ── Context modifiers ─────────────────────────────────────────────────────
    if context.get("is_exam_day"):
        # On exam day, raise bar — only truly critical things get through
        score -= 15

    if context.get("stress_level") in ["HIGH", "CRITICAL"]:
        # Under stress, suppress non-critical
        score -= 10

    time_to_event = context.get("time_to_critical_event_mins", 999)
    if time_to_event < 60:
        # Near critical event — suppress everything except CRITICAL
        for kw in CRITICAL_KEYWORDS:
            if kw in source_text:
                score += 20
                break
        else:
            score -= 20

    # ── App source modifiers ──────────────────────────────────────────────────
    app = item.get("app", "").lower()
    if app in ["whatsapp", "phone", "messages"]:
        score += 5   # personal comms
    elif app in ["instagram", "twitter", "youtube", "tiktok"]:
        score -= 30  # social media = noise
    elif app in ["gmail", "outlook", "email"]:
        score += 5

    # ── Clamp and classify ────────────────────────────────────────────────────
    score = max(0, min(100, score))
    item["priority_score"] = score
    item["surface"] = score >= 65

    if score >= 80:
        item["priority_level"] = "CRITICAL"
    elif score >= 65:
        item["priority_level"] = "HIGH"
    elif score >= 40:
        item["priority_level"] = "MEDIUM"
    else:
        item["priority_level"] = "LOW"

    return item


def filter_notifications(notifications: list, memory: dict, context: dict, max_surface: int = 3) -> dict:
    """
    Takes a raw list of notifications, scores all, surfaces top N.
    Returns surfaced list + full scored list + stats.
    """
    if not notifications:
        return {
            "surfaced": [],
            "all_scored": [],
            "suppressed_count": 0,
            "stats": {"total": 0, "surfaced": 0, "suppressed": 0}
        }

    scored = [score_item(n.copy(), memory, context) for n in notifications]
    scored.sort(key=lambda x: x["priority_score"], reverse=True)

    surfaced = [n for n in scored if n.get("surface")][:max_surface]
    suppressed_count = len(notifications) - len(surfaced)

    return {
        "surfaced":         surfaced,
        "all_scored":       scored,
        "suppressed_count": suppressed_count,
        "stats": {
            "total":     len(notifications),
            "surfaced":  len(surfaced),
            "suppressed": suppressed_count,
            "avg_score": round(sum(n["priority_score"] for n in scored) / len(scored))
        }
    }


def score_tasks(tasks: list, memory: dict, context: dict) -> list:
    """
    Scores and reorders a task list by true priority.
    Adds priority_score and priority_level to each task.
    """
    scored = []
    for task in tasks:
        t = task.copy()
        base = 50

        # Due date proximity
        due = t.get("due", "")
        if due == "today":
            base += 30
        elif due == "tomorrow":
            base += 15

        # Manual priority field
        manual = t.get("priority", 5)
        base += (5 - manual) * 5  # priority 1 = +20, priority 5 = 0

        # Task title keywords
        title_lower = t.get("title", "").lower()
        for kw in CRITICAL_KEYWORDS:
            if kw in title_lower:
                base += 15
                break

        base = max(0, min(100, base))
        t["priority_score"] = base
        t["priority_level"] = "CRITICAL" if base >= 80 else "HIGH" if base >= 60 else "MEDIUM" if base >= 40 else "LOW"
        scored.append(t)

    return sorted(scored, key=lambda x: x["priority_score"], reverse=True)
