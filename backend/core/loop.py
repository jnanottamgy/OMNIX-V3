"""
OMNIX — Master Agent Loop
Orchestrates the full cycle:
  Context Agent → Memory Agent (inject) → Planner Agent
  → Executor Agent → Memory Agent (commit)

Returns a complete, structured run log for the API and SSE stream.
"""
import time
from datetime import datetime

from agents.context_agent import build_context_snapshot
from agents.memory_agent import inject as memory_inject, commit as memory_commit
from agents.planner_agent import plan as planner_plan
from agents.executor_agent import execute_plan
from core.edge_rules import evaluate as edge_evaluate

# Shared SSE log buffer (in-memory for demo; resets on restart)
_log_buffer: list = []
_current_status: str = "IDLE"
_last_run_result: dict | None = None


def get_log_buffer() -> list:
    return _log_buffer


def get_status() -> str:
    return _current_status


def get_last_result() -> dict | None:
    return _last_run_result


def _log(agent: str, message: str, data: dict | None = None):
    """Appends a log entry to the SSE buffer."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "agent": agent,
        "message": message,
        "data": data or {}
    }
    _log_buffer.append(entry)
    print(f"[{agent}] {message}")
    return entry


def run_loop(scenario_override: dict | None = None) -> dict:
    """
    Executes one full OMNIX agent cycle.
    Returns the complete run log.
    """
    global _current_status, _last_run_result, _log_buffer

    _current_status = "RUNNING"
    run_start = time.time()
    run_log = []

    def log(agent, message, data=None):
        entry = _log(agent, message, data)
        run_log.append(entry)
        return entry

    try:
        # ── PHASE 1: Context Agent ───────────────────────────────────────────
        t0 = time.time()
        log("CONTEXT", "Collecting life signals from all sources...")
        context = build_context_snapshot(scenario_override)
        ctx_latency = round((time.time() - t0) * 1000)

        log("CONTEXT", f"Snapshot built in {ctx_latency}ms", {
            "urgency": context["urgency"],
            "sleep_score": context["sleep_score"],
            "energy": context["energy_estimate"],
            "is_exam_day": context["is_exam_day"],
            "time_to_exam_mins": context.get("time_to_critical_event_mins"),
            "breakfast": context["breakfast_consumed"],
        })

        # ── PHASE 2: Memory Agent (inject) ───────────────────────────────────
        t0 = time.time()
        log("MEMORY", "Injecting long-term memory into planning context...")
        memory = memory_inject()
        mem_latency = round((time.time() - t0) * 1000)

        log("MEMORY", f"Memory loaded in {mem_latency}ms — {len(memory.get('action_history', []))} past actions, loop #{memory.get('loop_count', 0) + 1}", {
            "behavioral_patterns_count": len(memory.get("behavioral_patterns", {})),
            "action_history_count": len(memory.get("action_history", [])),
        })

        # ── PHASE 3: Edge Rules Engine ───────────────────────────────────────
        t0 = time.time()
        log("EXECUTOR", "Running edge rule engine (local, deterministic)...")
        edge_actions = edge_evaluate(context, memory)
        edge_latency = round((time.time() - t0) * 1000)

        log("EXECUTOR", f"Edge engine fired {len(edge_actions)} rule(s) in {edge_latency}ms", {
            "edge_actions": [a["action_type"] for a in edge_actions]
        })

        # ── PHASE 4: Planner Agent (LLM) ─────────────────────────────────────
        t0 = time.time()
        log("PLANNER", "Calling cloud LLM for complex multi-factor reasoning...")
        action_plan, provider, planner_latency = planner_plan(context, memory)

        log("PLANNER", f"Plan generated via {provider} in {round(planner_latency * 1000)}ms — {len(action_plan)} action(s) queued", {
            "provider": provider,
            "actions_planned": [a.get("action_type") for a in action_plan],
            "latency_ms": round(planner_latency * 1000)
        })

        # ── PHASE 5: Executor Agent ───────────────────────────────────────────
        log("EXECUTOR", f"Executing {len(edge_actions)} edge + {len(action_plan)} cloud actions...")
        t0 = time.time()
        executed_actions = execute_plan(action_plan, edge_actions, context, memory)
        exec_latency = round((time.time() - t0) * 1000)

        edge_count = sum(1 for a in executed_actions if a.get("provider") == "edge")
        cloud_count = len(executed_actions) - edge_count

        log("EXECUTOR", f"All actions executed in {exec_latency}ms — {edge_count} edge, {cloud_count} cloud", {
            "total_executed": len(executed_actions),
            "edge_count": edge_count,
            "cloud_count": cloud_count
        })

        for action in executed_actions:
            log("EXECUTOR", f"✓ {action['action_type'].upper()} — {action['reasoning'][:80]}...", {
                "action_type": action["action_type"],
                "execution_path": action.get("provider"),
                "latency_ms": action.get("latency_ms"),
                "result": action.get("execution_result", {})
            })

        # ── PHASE 6: Memory Agent (commit) ────────────────────────────────────
        log("MEMORY", "Writing updated patterns to long-term memory...")
        t0 = time.time()
        memory_diff = memory_commit(context, executed_actions)
        commit_latency = round((time.time() - t0) * 1000)

        log("MEMORY", f"Memory updated in {commit_latency}ms — {len(memory_diff)} field(s) changed", {
            "diff": memory_diff
        })

        # ── LOOP COMPLETE ─────────────────────────────────────────────────────
        total_latency = round((time.time() - run_start) * 1000)
        log("SYSTEM", f"LOOP COMPLETE — {len(executed_actions)} actions in {total_latency}ms. Next cycle in 60s.", {
            "total_latency_ms": total_latency,
            "actions_executed": len(executed_actions),
            "edge_actions": edge_count,
            "cloud_actions": cloud_count
        })

        result = {
            "status": "COMPLETE",
            "run_at": datetime.now().isoformat(),
            "total_latency_ms": total_latency,
            "context_snapshot": context,
            "memory_snapshot": memory,
            "edge_actions": edge_actions,
            "action_plan": action_plan,
            "executed_actions": executed_actions,
            "memory_diff": memory_diff,
            "log": run_log,
            "stats": {
                "total_actions": len(executed_actions),
                "edge_count": edge_count,
                "cloud_count": cloud_count,
                "planner_provider": provider,
                "planner_latency_ms": round(planner_latency * 1000)
            }
        }

        _last_run_result = result
        _current_status = "COMPLETE"
        return result

    except Exception as e:
        log("SYSTEM", f"LOOP ERROR: {str(e)}", {"error": str(e)})
        _current_status = "ERROR"
        return {
            "status": "ERROR",
            "error": str(e),
            "log": run_log
        }
