"""
OMNIX — Context Agent
Collects, parses, and structures signals from the user's world into a
clean contextSnapshot{} dict that the Planner Agent can reason over.
"""
import json
import os
from datetime import datetime

MOCK_DIR = os.path.join(os.path.dirname(__file__), "..", "mock")


def _load(filename: str) -> dict:
    with open(os.path.join(MOCK_DIR, filename), "r") as f:
        return json.load(f)


def build_context_snapshot(scenario_override: dict | None = None) -> dict:
    """
    Build a structured context snapshot from mock data sources.
    If scenario_override is passed (e.g. from /scenario/inject), it
    merges on top of the baseline mock data.
    """
    calendar = _load("calendar.json")
    health = _load("health.json")
    notifications = _load("notifications.json")

    now = datetime.now()
    current_time = now.strftime("%H:%M")

    # Apply scenario overrides if provided
    if scenario_override:
        if "sleep_score" in scenario_override:
            health["sleep"]["quality_score"] = scenario_override["sleep_score"]
        if "wakeup_time" in scenario_override:
            health["sleep"]["wakeup_time"] = scenario_override["wakeup_time"]
        if "breakfast_consumed" in scenario_override:
            health["nutrition"]["breakfast_consumed"] = scenario_override["breakfast_consumed"]
        if "current_time" in scenario_override:
            current_time = scenario_override["current_time"]

    # ── Compute derived signals ──────────────────────────────────────────────
    sleep_score = health["sleep"]["quality_score"]
    wakeup_time = health["sleep"]["wakeup_time"]
    energy = health["current_vitals"]["energy_estimate"]
    breakfast = health["nutrition"]["breakfast_consumed"]

    # Find the next critical event and time remaining
    next_exam = None
    time_to_exam_mins = None
    for evt in calendar["events"]:
        if evt.get("urgency") == "CRITICAL":
            next_exam = evt
            # Calculate time to exam from current_time
            try:
                fmt = "%H:%M"
                now_t = datetime.strptime(current_time, fmt)
                exam_t = datetime.strptime(evt["start"], fmt)
                delta_mins = int((exam_t - now_t).total_seconds() / 60)
                time_to_exam_mins = delta_mins if delta_mins > 0 else 0
            except Exception:
                time_to_exam_mins = 73  # fallback

    # ── Urgency scoring ──────────────────────────────────────────────────────
    urgency = "NORMAL"
    urgency_score = 0

    if time_to_exam_mins is not None and time_to_exam_mins < 90:
        urgency_score += 40
    if sleep_score < 50:
        urgency_score += 25
    if not breakfast:
        urgency_score += 10
    if health["current_vitals"]["stress_indicator"] == "HIGH":
        urgency_score += 15

    if urgency_score >= 60:
        urgency = "CRITICAL"
    elif urgency_score >= 35:
        urgency = "HIGH"
    elif urgency_score >= 15:
        urgency = "MEDIUM"

    # ── Gym check ────────────────────────────────────────────────────────────
    gym_scheduled = any(
        "gym" in evt["title"].lower() for evt in calendar["events"]
    )

    snapshot = {
        "timestamp": now.isoformat(),
        "current_time": current_time,
        "date": calendar["date"],

        # Sleep
        "sleep_score": sleep_score,
        "sleep_hours": health["sleep"]["duration_hours"],
        "wakeup_time": wakeup_time,

        # Energy + stress
        "energy_estimate": energy,
        "stress_level": health["current_vitals"]["stress_indicator"],

        # Nutrition
        "breakfast_consumed": breakfast,
        "hydration_level": health["nutrition"]["hydration_level"],

        # Schedule
        "next_critical_event": next_exam,
        "time_to_critical_event_mins": time_to_exam_mins,
        "gym_scheduled": gym_scheduled,
        "all_events": calendar["events"],
        "pending_tasks": calendar["tasks_pending"],

        # Notifications
        "unread_notifications": notifications["unread_count"],
        "top_notifications": notifications["notifications"][:3],

        # Derived
        "urgency": urgency,
        "urgency_score": urgency_score,
        "is_exam_day": next_exam is not None,
        "exam_flag": next_exam is not None,
    }

    return snapshot
