"""
OMNIX — Executor Agent
Routes each action through the complexity classifier:
  - low complexity  → edge_rules local execution
  - high complexity → LLM execution call
Logs everything with timestamps and latency.
"""
import json
import os
import time
import httpx
from datetime import datetime

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

EXECUTOR_SYSTEM_PROMPT = """You are the Executor Agent in OMNIX. You receive a specific action to perform and must generate the exact output to execute it.

Rules:
1. Return ONLY a JSON object. No preamble. No markdown.
2. The object must have a "result" key with the execution output.
3. Be specific, concise, and actionable.
4. For messages: generate natural, human-sounding text matching the tone in memory."""

EXECUTOR_USER_TEMPLATE = """Execute this action:
Action Type: {action_type}
Details: {details}
Context: {context_summary}
Memory: {memory_summary}

Return the execution result as a JSON object with a "result" key."""


def _execute_via_llm(action: dict, context: dict, memory: dict) -> tuple[dict, float]:
    """Calls LLM to execute a high-complexity action."""
    start = time.time()

    context_summary = {
        "time": context.get("current_time"),
        "exam_in_mins": context.get("time_to_critical_event_mins"),
        "sleep_score": context.get("sleep_score"),
        "is_exam_day": context.get("is_exam_day")
    }
    memory_summary = {
        "weak_subjects": memory.get("academic_profile", {}).get("weak_subjects", []),
        "tone": memory.get("personalization", {}).get("communication_tone", "concise"),
        "revision_style": memory.get("academic_profile", {}).get("revision_style", "standard")
    }

    user_msg = EXECUTOR_USER_TEMPLATE.format(
        action_type=action.get("action_type"),
        details=json.dumps(action.get("details", {})),
        context_summary=json.dumps(context_summary),
        memory_summary=json.dumps(memory_summary)
    )

    # Try Gemini
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "system_instruction": {"parts": [{"text": EXECUTOR_SYSTEM_PROMPT}]},
                "contents": [{"parts": [{"text": user_msg}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
            }
            with httpx.Client(timeout=12.0) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                result = json.loads(text)
                return {"output": result, "provider": "gemini"}, round(time.time() - start, 3)
        except Exception as e:
            print(f"[EXECUTOR] Gemini failed: {e}")

    # Try OpenAI
    if OPENAI_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": EXECUTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg}
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"}
            }
            with httpx.Client(timeout=12.0) as client:
                resp = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                result = json.loads(data["choices"][0]["message"]["content"])
                return {"output": result, "provider": "openai"}, round(time.time() - start, 3)
        except Exception as e:
            print(f"[EXECUTOR] OpenAI failed: {e}")

    # Local fallback
    return _local_execute(action, context, memory), round(time.time() - start, 3)


def _local_execute(action: dict, context: dict, memory: dict) -> dict:
    """Local fallback execution — generates realistic outputs without LLM."""
    atype = action.get("action_type")
    details = action.get("details", {})

    if atype == "message":
        return {
            "output": {
                "result": {
                    "to": details.get("to", "Contact"),
                    "channel": details.get("channel", "WhatsApp"),
                    "body": details.get("body", "Hey, quick heads up — running behind schedule. Will update soon!"),
                    "sent": True
                }
            },
            "provider": "local"
        }
    elif atype == "study_plan":
        subject = details.get("subject", "Mathematics")
        duration = details.get("duration_mins", 30)
        return {
            "output": {
                "result": {
                    "subject": subject,
                    "plan": [
                        {"slot": "0-10min", "topic": "Quick formula sheet review", "method": "active_recall"},
                        {"slot": "10-25min", "topic": details.get("topics", ["Core concepts"])[0] if details.get("topics") else "Core concepts", "method": "practice_problems"},
                        {"slot": f"25-{duration}min", "topic": "Past paper questions — high-yield only", "method": "timed_practice"}
                    ],
                    "key_reminder": "Focus on what you KNOW. Don't open new topics now."
                }
            },
            "provider": "local"
        }
    elif atype == "food_order":
        return {
            "output": {
                "result": {
                    "item": details.get("item", "Protein wrap + coffee"),
                    "vendor": details.get("vendor", "Campus Canteen"),
                    "eta_mins": details.get("eta_mins", 12),
                    "order_id": f"OMX-{int(time.time()) % 10000}",
                    "status": "placed"
                }
            },
            "provider": "local"
        }
    else:
        return {
            "output": {"result": {"status": "executed", "details": details}},
            "provider": "local"
        }


def _execute_edge(action: dict) -> tuple[dict, float]:
    """Executes a low-complexity action via the edge rule engine (instant)."""
    start = time.time()
    # Edge actions are already determined; we just log/confirm execution
    result = {
        "output": {"result": {"status": "executed", "details": action.get("details", {})}},
        "provider": "edge"
    }
    # Simulate real edge latency (5-20ms)
    time.sleep(0.012)
    return result, round(time.time() - start, 3)


def execute_plan(action_plan: list, edge_actions: list, context: dict, memory: dict) -> list:
    """
    Executes all actions. Edge actions run locally, high-complexity via LLM.
    Returns the full execution log.
    """
    executed = []

    # Execute edge-triggered actions first
    for action in edge_actions:
        exec_result, latency = _execute_edge(action)
        log_entry = {
            **action,
            "executed_at": datetime.now().isoformat(),
            "latency_ms": round(latency * 1000),
            "execution_result": exec_result.get("output", {}),
            "provider": "edge",
            "status": "EXECUTED"
        }
        executed.append(log_entry)

    # Execute LLM-planned actions
    for action in action_plan:
        complexity = action.get("complexity", "low")

        if complexity == "high":
            exec_result, latency = _execute_via_llm(action, context, memory)
            provider = exec_result.get("provider", "llm")
        else:
            exec_result, latency = _execute_edge(action)
            provider = "edge"

        log_entry = {
            **action,
            "executed_at": datetime.now().isoformat(),
            "latency_ms": round(latency * 1000),
            "execution_result": exec_result.get("output", {}),
            "provider": provider,
            "status": "EXECUTED"
        }
        executed.append(log_entry)

    return executed
