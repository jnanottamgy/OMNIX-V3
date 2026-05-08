"""
OMNIX — Memory Agent
Handles two operations:
  - inject(): reads long-term memory to enrich Planner context
  - commit(): writes new patterns back after Executor completes
"""
import json
import os
from datetime import datetime
from copy import deepcopy

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "memory", "user_memory.json")


def _load_memory() -> dict:
    with open(MEMORY_PATH, "r") as f:
        return json.load(f)


def _save_memory(memory: dict):
    memory["last_updated"] = datetime.now().isoformat()
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)


def inject() -> dict:
    """
    Returns the current long-term memory snapshot.
    Called by the Planner Agent before every reasoning cycle.
    """
    return _load_memory()


def commit(context_snapshot: dict, executed_actions: list) -> dict:
    """
    Updates long-term memory based on what just happened.
    Returns a dict describing what changed (the memory diff).
    """
    memory = _load_memory()
    diff = {}

    sleep_score = context_snapshot.get("sleep_score", 100)
    breakfast = context_snapshot.get("breakfast_consumed", True)
    is_exam_day = context_snapshot.get("is_exam_day", False)
    gym_scheduled = context_snapshot.get("gym_scheduled", False)

    # ── Update behavioral patterns ───────────────────────────────────────────
    if sleep_score < 60:
        trend = memory["behavioral_patterns"]["sleep_quality_trend"]
        if trend != "consistently_low":
            memory["behavioral_patterns"]["sleep_quality_trend"] = "consistently_low"
            diff["sleep_quality_trend"] = "consistently_low"

    if not breakfast and context_snapshot.get("wakeup_time"):
        memory["behavioral_patterns"]["breakfast_habit"] = "skips_when_late"
        diff["breakfast_habit"] = "skips_when_late"

    # ── Update gym_skip_triggers ─────────────────────────────────────────────
    gym_was_cancelled = any(
        a.get("action_type") == "reschedule" and "gym" in str(a.get("details", "")).lower()
        for a in executed_actions
    )
    if gym_was_cancelled:
        triggers = set(memory["behavioral_patterns"]["gym_skip_triggers"])
        if is_exam_day:
            triggers.add("exam_day")
        if sleep_score < 60:
            triggers.add("poor_sleep")
        memory["behavioral_patterns"]["gym_skip_triggers"] = list(triggers)
        diff["gym_skip_triggers"] = list(triggers)

    # ── Append to action history (last 20 only) ──────────────────────────────
    for action in executed_actions:
        record = {
            "date": context_snapshot.get("date", datetime.now().strftime("%Y-%m-%d")),
            "action": action.get("action_type"),
            "trigger": action.get("reasoning", "")[:80],
            "outcome": "executed",
            "execution_path": action.get("execution_path", "unknown"),
            "latency_ms": action.get("latency_ms", 0)
        }
        memory["action_history"].append(record)

    # Keep only last 20 actions
    memory["action_history"] = memory["action_history"][-20:]

    # ── Increment loop count ─────────────────────────────────────────────────
    memory["loop_count"] = memory.get("loop_count", 0) + 1
    diff["loop_count"] = memory["loop_count"]

    _save_memory(memory)
    return diff


def reset_memory():
    """Resets memory to clean defaults."""
    default = {
        "schema_version": "1.0",
        "last_updated": None,
        "behavioral_patterns": {
            "sleep_quality_trend": "consistently_low",
            "breakfast_habit": "skips_when_late",
            "productivity_peak_hours": ["19:00", "22:00"],
            "gym_skip_triggers": ["poor_sleep", "exam_day"],
            "preferred_task_batching": "evening",
            "avg_sleep_hours": 5.8
        },
        "academic_profile": {
            "weak_subjects": ["mathematics", "physics"],
            "strong_subjects": ["computer_science", "algorithms"],
            "exam_anxiety_score": 0.72,
            "revision_style": "spaced_repetition",
            "current_semester": 5,
            "institution": "MSRIT"
        },
        "action_history": [],
        "personalization": {
            "communication_tone": "concise",
            "notification_sensitivity": "low",
            "preferred_food_type": "high_protein",
            "name": "User"
        },
        "loop_count": 0
    }
    _save_memory(default)
    return default
