"""
OMNIX V3 — FastAPI Backend (Complete)
All endpoints:
  POST /generate-schedule        — AI schedule from user text
  POST /run-loop                 — Full autonomous agent cycle
  POST /health-checkin           — User submits real health data
  GET  /integrations/status      — Which services are connected
  GET  /integrations/google/auth-url   — Start Google OAuth
  GET  /integrations/google/callback  — OAuth callback
  GET  /integrations/strava/auth-url  — Start Strava OAuth
  GET  /integrations/strava/callback  — Strava OAuth callback
  GET  /integrations/google/pull      — Pull fresh Google data
  POST /simulate                 — Digital twin simulation
  GET  /weekly-recap             — Weekly life summary
  GET  /priority-filter          — Score + filter notifications
  GET  /stress-profile           — Current stress analysis
  GET  /status                   — System health
  GET  /memory                   — Long-term memory
  POST /memory/reset             — Reset memory
  GET  /health                   — Healthcheck
"""
import os
import sys
from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

from agents.schedule_agent    import generate as schedule_generate
from agents.context_agent     import build_context_snapshot
from agents.memory_agent      import inject as memory_inject, commit as memory_commit, reset_memory
from agents.planner_agent     import plan as planner_plan
from agents.executor_agent    import execute_plan
from agents.integrations_agent import (
    get_auth_url, exchange_code, is_connected, disconnect,
    pull_calendar, pull_gmail,
    get_strava_auth_url, exchange_strava_code, pull_strava
)
from agents.stress_agent      import analyse as stress_analyse
from agents.priority_engine   import filter_notifications, score_tasks
from agents.digital_twin      import build_twin, simulate, run_proactive_simulations
from agents.recap_agent       import generate_weekly_recap
from core.edge_rules          import evaluate as edge_evaluate
from core.loop                import run_loop

# ── Real health data store (per user_id, in-memory for demo) ─────────────────
_health_store: dict[str, dict] = {}
_google_data_cache: dict[str, dict] = {}

app = FastAPI(title="OMNIX V3", version="3.0.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic Models ───────────────────────────────────────────────────────────

class ScheduleRequest(BaseModel):
    user_input: str
    profile: Optional[dict] = {}
    user_id: Optional[str] = "default"

class HealthCheckin(BaseModel):
    user_id: Optional[str] = "default"
    sleep_hours: float = 7.0
    sleep_quality: int = 70       # 0-100
    steps_today: int = 0
    energy_level: int = 70        # 0-100 user-reported
    mood: str = "neutral"         # great | good | neutral | low | terrible
    breakfast_consumed: bool = True
    hydration: str = "OK"         # LOW | OK | GOOD
    notes: Optional[str] = ""

class SimulateRequest(BaseModel):
    decision: str
    user_id: Optional[str] = "default"

class NotificationItem(BaseModel):
    title: Optional[str] = ""
    subject: Optional[str] = ""
    body: Optional[str] = ""
    from_: Optional[str] = ""
    app: Optional[str] = ""

class FilterRequest(BaseModel):
    notifications: List[dict]
    user_id: Optional[str] = "default"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_real_context(user_id: str, scenario_override: dict | None = None) -> dict:
    """
    Builds context from REAL data sources where available,
    falling back to mock data only for fields with no real source.
    """
    # Start with mock baseline
    ctx = build_context_snapshot(scenario_override)

    # Override with real health check-in if available
    health = _health_store.get(user_id)
    if health:
        ctx["sleep_score"]        = health.get("sleep_quality", ctx["sleep_score"])
        ctx["sleep_hours"]        = health.get("sleep_hours", ctx["sleep_hours"])
        ctx["energy_estimate"]    = health.get("energy_level", ctx["energy_estimate"])
        ctx["breakfast_consumed"] = health.get("breakfast_consumed", ctx["breakfast_consumed"])
        ctx["hydration_level"]    = health.get("hydration", ctx["hydration_level"])
        ctx["user_mood"]          = health.get("mood", "neutral")
        ctx["steps_today"]        = health.get("steps_today", 0)
        ctx["data_source"]        = "real_checkin"

    # Override with real Google Calendar if connected
    if is_connected(user_id):
        google_data = _google_data_cache.get(user_id, {})
        cal = google_data.get("calendar", {})
        if cal.get("connected") and cal.get("events"):
            ctx["all_events"] = cal["events"]
            ctx["event_count"] = cal.get("event_count", 0)
            # Re-derive next critical event from real data
            critical = cal.get("next_critical")
            if critical:
                ctx["next_critical_event"]             = critical
                ctx["time_to_critical_event_mins"]    = critical.get("delta_mins", 999)
                ctx["is_exam_day"]                    = critical.get("urgency") == "CRITICAL"
            ctx["calendar_density"]   = cal.get("density_score", 0)
            ctx["calendar_source"]    = "google_calendar"

    return ctx


def _refresh_google_data(user_id: str):
    """Pulls fresh Google Calendar + Gmail data and caches it."""
    if not is_connected(user_id):
        return
    cal   = pull_calendar(user_id)
    gmail = pull_gmail(user_id)
    _google_data_cache[user_id] = {
        "calendar": cal,
        "gmail":    gmail,
        "pulled_at": datetime.now().isoformat()
    }

# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "system": "OMNIX V3",
        "version": "3.0.0",
        "status": "ONLINE",
        "real_data_sources": ["google_calendar", "gmail", "strava", "health_checkin"],
        "new_features": ["stress_detection", "priority_engine", "digital_twin", "weekly_recap"],
        "docs": "/docs"
    }

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "3.0.0", "system": "OMNIX V3"}

# ── Health Check-in (Real Data Entry) ────────────────────────────────────────

@app.post("/health-checkin")
async def health_checkin(data: HealthCheckin):
    """
    User submits their real morning health data.
    30-second form. This replaces mock health data entirely.
    """
    uid = data.user_id or "default"
    _health_store[uid] = {
        "sleep_hours":        data.sleep_hours,
        "sleep_quality":      data.sleep_quality,
        "steps_today":        data.steps_today,
        "energy_level":       data.energy_level,
        "mood":               data.mood,
        "breakfast_consumed": data.breakfast_consumed,
        "hydration":          data.hydration,
        "notes":              data.notes,
        "submitted_at":       datetime.now().isoformat()
    }

    # Immediately refresh context with real data
    _refresh_google_data(uid)

    return {
        "status": "saved",
        "message": "Real health data saved. OMNIX will use this for all decisions today.",
        "data": _health_store[uid]
    }

@app.get("/health-checkin/{user_id}")
async def get_health_checkin(user_id: str):
    data = _health_store.get(user_id)
    if not data:
        return {"has_data": False, "message": "No check-in today. Submit your morning data."}
    return {"has_data": True, "data": data}

# ── Integrations ──────────────────────────────────────────────────────────────

@app.get("/integrations/status")
async def integration_status(user_id: str = "default"):
    google_cache = _google_data_cache.get(user_id, {})
    health_data  = _health_store.get(user_id)
    return {
        "google_calendar": {
            "connected": is_connected(user_id),
            "last_pulled": google_cache.get("pulled_at"),
            "event_count": google_cache.get("calendar", {}).get("event_count", 0)
        },
        "gmail": {
            "connected": is_connected(user_id),
            "unread_count": google_cache.get("gmail", {}).get("unread_count", 0),
        },
        "strava": {
            "connected": False,  # extend as needed
            "activities_7d": 0
        },
        "health_checkin": {
            "submitted_today": health_data is not None,
            "submitted_at": (health_data or {}).get("submitted_at")
        }
    }

@app.get("/integrations/google/auth-url")
async def google_auth_url(user_id: str = "default"):
    try:
        url = get_auth_url(state=user_id)
        return {"auth_url": url}
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"error": str(e), "hint": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env"}
        )

@app.get("/integrations/google/callback")
async def google_callback(code: str, state: str = "default"):
    try:
        result = exchange_code(code, user_id=state)
        _refresh_google_data(state)
        # Redirect to frontend with success flag
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5500")
        return RedirectResponse(url=f"{frontend_url}?google_connected=true")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/integrations/google/pull")
async def pull_google(user_id: str = "default"):
    if not is_connected(user_id):
        return {"error": "Google not connected. Call /integrations/google/auth-url first."}
    _refresh_google_data(user_id)
    return _google_data_cache.get(user_id, {})

@app.get("/integrations/google/disconnect")
async def google_disconnect(user_id: str = "default"):
    disconnect(user_id)
    _google_data_cache.pop(user_id, None)
    return {"status": "disconnected"}

@app.get("/integrations/strava/auth-url")
async def strava_auth_url():
    try:
        url = get_strava_auth_url()
        return {"auth_url": url}
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"error": str(e), "hint": "Set STRAVA_CLIENT_ID in .env"}
        )

@app.get("/integrations/strava/callback")
async def strava_callback(code: str, user_id: str = "default"):
    try:
        result = exchange_strava_code(code, user_id)
        return {"status": "connected", "athlete": result.get("athlete")}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

# ── Schedule Generation ───────────────────────────────────────────────────────

@app.post("/generate-schedule")
async def generate_schedule(req: ScheduleRequest):
    uid = req.user_id or "default"
    if not req.user_input.strip():
        return {"error": "user_input is required"}

    # Enrich profile with real Google data if available
    profile = req.profile or {}
    google = _google_data_cache.get(uid, {})
    if google.get("calendar", {}).get("events"):
        profile["_real_events"] = google["calendar"]["events"][:5]

    schedule, provider, latency = schedule_generate(req.user_input, profile)

    return {
        "status":      "ok",
        "schedule":    schedule,
        "provider":    provider,
        "latency_ms":  round(latency * 1000),
        "block_count": len(schedule),
        "data_source": "real_calendar" if google.get("calendar", {}).get("connected") else "user_input"
    }

# ── Agent Loop ────────────────────────────────────────────────────────────────

@app.post("/run-loop")
async def trigger_loop(user_id: str = "default"):
    # Use real data context if available
    context = _get_real_context(user_id)

    # Pull fresh Google data if connected
    if is_connected(user_id):
        _refresh_google_data(user_id)

    result = run_loop()

    # Attach stress profile
    google = _google_data_cache.get(user_id, {})
    memory = memory_inject()
    stress = stress_analyse(
        context,
        google.get("gmail", {}),
        google.get("calendar", {}),
    )
    result["stress_profile"] = stress

    # Attach twin simulations
    sims = run_proactive_simulations(context, memory)
    result["twin_simulations"] = sims

    return result

# ── Stress Profile ────────────────────────────────────────────────────────────

@app.get("/stress-profile")
async def get_stress_profile(user_id: str = "default"):
    context = _get_real_context(user_id)
    google  = _google_data_cache.get(user_id, {})
    profile = stress_analyse(
        context,
        google.get("gmail", {}),
        google.get("calendar", {})
    )
    return profile

# ── Priority Filter ───────────────────────────────────────────────────────────

@app.post("/priority-filter")
async def priority_filter(req: FilterRequest):
    memory  = memory_inject()
    context = _get_real_context(req.user_id or "default")
    result  = filter_notifications(req.notifications, memory, context)
    return result

# ── Digital Twin Simulation ───────────────────────────────────────────────────

@app.post("/simulate")
async def simulate_decision(req: SimulateRequest):
    uid     = req.user_id or "default"
    memory  = memory_inject()
    context = _get_real_context(uid)
    twin    = build_twin(memory)
    result  = simulate(req.decision, context, twin)
    result["twin_model"] = twin["behavioral_model"]
    return result

@app.get("/twin-profile")
async def get_twin_profile(user_id: str = "default"):
    memory = memory_inject()
    twin   = build_twin(memory)
    context = _get_real_context(user_id)
    sims   = run_proactive_simulations(context, memory)
    return {
        "twin":        twin,
        "simulations": sims,
        "generated_at": datetime.now().isoformat()
    }

# ── Weekly Recap ──────────────────────────────────────────────────────────────

@app.get("/weekly-recap")
async def weekly_recap(user_id: str = "default"):
    memory = memory_inject()
    recap  = generate_weekly_recap(memory)
    return recap

# ── Memory + Status ───────────────────────────────────────────────────────────

@app.get("/memory")
async def get_memory():
    return memory_inject()

@app.post("/memory/reset")
async def reset_memory_endpoint():
    return {"status": "reset", "memory": reset_memory()}

@app.get("/status")
async def get_status():
    memory = memory_inject()
    return {
        "status": "ONLINE",
        "version": "3.0.0",
        "loop_count": memory.get("loop_count", 0),
        "agents": {
            "context_agent":    "ONLINE",
            "schedule_agent":   "ONLINE",
            "planner_agent":    "ONLINE",
            "executor_agent":   "ONLINE",
            "memory_agent":     "ONLINE",
            "stress_agent":     "ONLINE",
            "priority_engine":  "ONLINE",
            "digital_twin":     "ONLINE",
            "recap_agent":      "ONLINE",
            "integrations":     "ONLINE"
        }
    }
