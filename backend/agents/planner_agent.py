"""
OMNIX — Planner Agent
Receives contextSnapshot + longTermMemory, calls LLM for complex reasoning,
returns a prioritized actionPlan[].
Falls back gracefully if no API key is configured.
"""
import json
import os
import time
import httpx
from datetime import datetime

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

PLANNER_SYSTEM_PROMPT = """You are the Planner Agent in OMNIX — an autonomous personal AI operating system.

Your role is to analyze the user's current context and long-term memory, then produce a ranked, actionable plan. Be decisive. Be specific. Explain your reasoning clearly.

Rules:
1. ALWAYS output valid JSON — an array of action objects. Nothing else.
2. Each action must include: action_type, priority, reasoning, complexity, estimated_impact, details
3. action_type must be one of: reschedule, message, focus_mode, food_order, reminder, study_plan
4. priority: integer 1-5 (1 = highest urgency)
5. complexity: "low" (edge rule handles it) or "high" (needs LLM execution)
6. reasoning: 1-2 sentences in plain English. This is shown to the user.
7. details: a dict with action-specific fields
8. Return ONLY the JSON array. No preamble. No markdown. No explanation outside JSON."""

PLANNER_USER_TEMPLATE = """Context Snapshot:
{context_json}

Long-Term Memory:
{memory_json}

Generate a comprehensive action plan for this moment. Prioritize by urgency and impact. Include 3-6 actions. Return ONLY the JSON array."""


def _call_gemini(context: dict, memory: dict) -> list:
    """Calls Google Gemini 1.5 Flash for planning."""
    if not GEMINI_API_KEY:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

    context_str = json.dumps(context, indent=2, default=str)
    memory_str = json.dumps(memory, indent=2, default=str)
    user_msg = PLANNER_USER_TEMPLATE.format(context_json=context_str, memory_json=memory_str)

    payload = {
        "system_instruction": {"parts": [{"text": PLANNER_SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_msg}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json"
        }
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
    except Exception as e:
        print(f"[PLANNER] Gemini call failed: {e}")
        return None


def _call_openai(context: dict, memory: dict) -> list:
    """Fallback: calls OpenAI GPT-4o Mini."""
    if not OPENAI_API_KEY:
        return None

    context_str = json.dumps(context, indent=2, default=str)
    memory_str = json.dumps(memory, indent=2, default=str)
    user_msg = PLANNER_USER_TEMPLATE.format(context_json=context_str, memory_json=memory_str)

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            parsed = json.loads(text)
            # Handle if LLM wraps in an object
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return parsed.get("actions", parsed.get("action_plan", list(parsed.values())[0]))
    except Exception as e:
        print(f"[PLANNER] OpenAI call failed: {e}")
        return None


def _local_fallback_plan(context: dict, memory: dict) -> list:
    """
    Rule-based fallback plan when no LLM API key is set.
    Produces a realistic plan so the demo works 100% offline.
    """
    plan = []
    sleep_score = context.get("sleep_score", 100)
    is_exam_day = context.get("is_exam_day", False)
    breakfast = context.get("breakfast_consumed", True)
    time_to_exam = context.get("time_to_critical_event_mins", 999)
    energy = context.get("energy_estimate", 80)
    weak_subjects = memory.get("academic_profile", {}).get("weak_subjects", [])

    if is_exam_day and time_to_exam and time_to_exam < 90:
        plan.append({
            "action_type": "study_plan",
            "priority": 1,
            "complexity": "high",
            "reasoning": f"Exam in {time_to_exam} mins. Memory shows {weak_subjects[0] if weak_subjects else 'the subject'} is a weak area. Spaced-repetition revision plan generated.",
            "details": {
                "subject": "Mathematics",
                "topics": ["Calculus derivatives", "Integration by parts", "Differential equations"],
                "duration_mins": min(time_to_exam - 15, 45),
                "method": "spaced_repetition",
                "confidence_target": 0.75
            },
            "estimated_impact": "Covers highest-yield weak topics before exam"
        })

    plan.append({
        "action_type": "message",
        "priority": 2,
        "complexity": "high",
        "reasoning": "Study group asked if you're coming at 09:30. With gym cancelled and exam prep ongoing, they need to know.",
        "details": {
            "to": "Study Group",
            "channel": "WhatsApp",
            "body": "Hey — can't make 09:30, focused on exam prep. Will catch you at 14:00 for the CS session. All good!"
        },
        "estimated_impact": "Manages social commitments without cognitive overhead"
    })

    if sleep_score < 60:
        plan.append({
            "action_type": "reminder",
            "priority": 3,
            "complexity": "low",
            "reasoning": f"Sleep score {sleep_score}/100 detected. Memory confirms low sleep correlates with higher exam anxiety for this user.",
            "details": {
                "message": "Deep breaths. You've prepared. Low sleep doesn't mean poor performance — focus on what you know.",
                "type": "motivational"
            },
            "estimated_impact": "Reduces exam anxiety by grounding cognitive state"
        })

    return plan


def plan(context: dict, memory: dict) -> tuple[list, str, float]:
    """
    Main planning function.
    Returns: (action_plan, provider_used, latency_seconds)
    """
    start = time.time()

    # Try Gemini first
    result = _call_gemini(context, memory)
    if result:
        return result, "gemini-1.5-flash", round(time.time() - start, 3)

    # Fallback to OpenAI
    result = _call_openai(context, memory)
    if result:
        return result, "gpt-4o-mini", round(time.time() - start, 3)

    # Local fallback (no API key)
    result = _local_fallback_plan(context, memory)
    return result, "local-fallback", round(time.time() - start, 3)
