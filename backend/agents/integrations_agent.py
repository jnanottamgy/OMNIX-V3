"""
OMNIX V3 — Google Integration Agent
Handles OAuth2 flow for Google Calendar + Gmail.
Returns real user data: events, email load, sender patterns.

Flow:
  1. Frontend calls GET /integrations/google/auth-url
  2. User authorizes in popup
  3. Google redirects to GET /integrations/google/callback?code=...
  4. Backend exchanges code → tokens, stores in session
  5. Any agent can call pull_google_data(user_id) to get real data
"""
import os
import json
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import httpx

# ── Config ────────────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI         = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/integrations/google/callback")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# In-memory token store (for demo — use Supabase in production)
_token_store: dict[str, dict] = {}

# ── OAuth Flow ────────────────────────────────────────────────────────────────

def get_auth_url(state: str = "omnix") -> str:
    """Returns the Google OAuth consent URL."""
    if not GOOGLE_CLIENT_ID:
        raise ValueError("GOOGLE_CLIENT_ID not set in .env")

    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state
    )
    return auth_url


def exchange_code(code: str, user_id: str) -> dict:
    """Exchanges auth code for tokens and stores them."""
    flow = _build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes or []),
        "connected_at":  datetime.now().isoformat()
    }
    _token_store[user_id] = token_data
    return {"status": "connected", "scopes": token_data["scopes"]}


def is_connected(user_id: str) -> bool:
    return user_id in _token_store


def disconnect(user_id: str):
    _token_store.pop(user_id, None)


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uris": [REDIRECT_URI],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)


def _get_creds(user_id: str) -> Optional[Credentials]:
    data = _token_store.get(user_id)
    if not data:
        return None
    return Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id", GOOGLE_CLIENT_ID),
        client_secret=data.get("client_secret", GOOGLE_CLIENT_SECRET),
        scopes=data.get("scopes", SCOPES),
    )

# ── Calendar Data ─────────────────────────────────────────────────────────────

def pull_calendar(user_id: str) -> dict:
    """
    Pulls today's + tomorrow's Google Calendar events.
    Returns structured data ready for Context Agent.
    """
    creds = _get_creds(user_id)
    if not creds:
        return {"connected": False, "events": [], "error": "Not connected"}

    try:
        service = build("calendar", "v3", credentials=creds)
        now = datetime.now(timezone.utc)
        tomorrow_end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0)

        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=tomorrow_end.isoformat(),
            maxResults=20,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        raw_events = result.get("items", [])
        events = []

        for e in raw_events:
            start_str = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")
            end_str   = e.get("end",   {}).get("dateTime") or e.get("end",   {}).get("date", "")

            # Parse time
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                start_time = start_dt.strftime("%H:%M")
                # Compute time to event
                delta_mins = int((start_dt - now).total_seconds() / 60)
            except Exception:
                start_time = "00:00"
                delta_mins = 999

            # Classify urgency
            title_lower = e.get("summary", "").lower()
            urgency = "LOW"
            if any(w in title_lower for w in ["exam", "test", "interview", "deadline", "submit", "review"]):
                urgency = "CRITICAL"
            elif any(w in title_lower for w in ["meeting", "call", "sync", "standup", "presentation"]):
                urgency = "MEDIUM"
            elif delta_mins < 60:
                urgency = "HIGH"

            events.append({
                "id":          e.get("id"),
                "title":       e.get("summary", "Untitled"),
                "start":       start_time,
                "start_iso":   start_str,
                "end_iso":     end_str,
                "location":    e.get("location", ""),
                "description": (e.get("description") or "")[:200],
                "urgency":     urgency,
                "delta_mins":  delta_mins,
                "attendees":   len(e.get("attendees", [])),
            })

        # Calendar density score (stress signal)
        density_score = min(len(events) * 10, 100)

        return {
            "connected":     True,
            "events":        events,
            "event_count":   len(events),
            "density_score": density_score,
            "next_critical": next((e for e in events if e["urgency"] == "CRITICAL"), None),
            "pulled_at":     now.isoformat()
        }

    except Exception as ex:
        return {"connected": True, "events": [], "error": str(ex)}

# ── Gmail Data ────────────────────────────────────────────────────────────────

def pull_gmail(user_id: str) -> dict:
    """
    Pulls Gmail inbox signals:
    - Unread count
    - Top senders (frequency)
    - Stress signals (dense inbox = high mental load)
    - Priority emails (from known important senders)
    Does NOT read email bodies — only metadata for privacy.
    """
    creds = _get_creds(user_id)
    if not creds:
        return {"connected": False, "unread_count": 0, "error": "Not connected"}

    try:
        service = build("gmail", "v1", credentials=creds)

        # Unread count
        profile = service.users().getProfile(userId="me").execute()
        total_messages = profile.get("messagesTotal", 0)

        # Unread in inbox
        unread_result = service.users().messages().list(
            userId="me", q="is:unread in:inbox", maxResults=50
        ).execute()
        unread_msgs = unread_result.get("messages", [])
        unread_count = len(unread_msgs)

        # Fetch metadata of recent unread messages
        sender_freq: dict[str, int] = {}
        priority_signals = []

        for msg_ref in unread_msgs[:15]:
            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"]
                ).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                sender = headers.get("From", "unknown")
                subject = headers.get("Subject", "")[:80]

                # Track sender frequency
                sender_email = sender.split("<")[-1].strip(">") if "<" in sender else sender
                sender_freq[sender_email] = sender_freq.get(sender_email, 0) + 1

                # Priority detection
                subj_lower = subject.lower()
                is_priority = any(w in subj_lower for w in [
                    "urgent", "asap", "deadline", "important", "action required",
                    "exam", "result", "offer", "interview", "payment"
                ])
                if is_priority:
                    priority_signals.append({
                        "from":    sender_email[:40],
                        "subject": subject,
                        "priority": "HIGH"
                    })

            except Exception:
                pass

        # Top senders
        top_senders = sorted(sender_freq.items(), key=lambda x: x[1], reverse=True)[:5]

        # Email stress score: unread count → stress signal
        stress_from_email = min(unread_count * 2, 100)

        return {
            "connected":          True,
            "unread_count":       unread_count,
            "total_messages":     total_messages,
            "priority_emails":    priority_signals[:3],
            "top_senders":        [{"email": s[0], "count": s[1]} for s in top_senders],
            "email_stress_score": stress_from_email,
            "pulled_at":          datetime.now().isoformat()
        }

    except Exception as ex:
        return {"connected": True, "unread_count": 0, "error": str(ex)}

# ── Strava Data ───────────────────────────────────────────────────────────────

_strava_tokens: dict[str, dict] = {}

STRAVA_CLIENT_ID     = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
STRAVA_REDIRECT_URI  = os.getenv("STRAVA_REDIRECT_URI", "http://localhost:8000/integrations/strava/callback")


def get_strava_auth_url() -> str:
    if not STRAVA_CLIENT_ID:
        raise ValueError("STRAVA_CLIENT_ID not set")
    return (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&redirect_uri={STRAVA_REDIRECT_URI}"
        f"&response_type=code"
        f"&approval_prompt=auto"
        f"&scope=read,activity:read"
    )


def exchange_strava_code(code: str, user_id: str) -> dict:
    resp = httpx.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code"
    })
    data = resp.json()
    _strava_tokens[user_id] = data
    return {"status": "connected", "athlete": data.get("athlete", {}).get("firstname", "Athlete")}


def pull_strava(user_id: str) -> dict:
    """Pulls recent Strava activities: runs, rides, steps."""
    token_data = _strava_tokens.get(user_id)
    if not token_data:
        return {"connected": False, "activities": []}

    access_token = token_data.get("access_token", "")
    try:
        resp = httpx.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": 7, "after": int((datetime.now() - timedelta(days=7)).timestamp())}
        )
        activities = resp.json()

        parsed = []
        total_distance_km = 0
        total_duration_min = 0

        for a in activities:
            dist_km = round((a.get("distance", 0)) / 1000, 2)
            dur_min = round(a.get("moving_time", 0) / 60)
            total_distance_km += dist_km
            total_duration_min += dur_min

            parsed.append({
                "name":         a.get("name", "Activity"),
                "type":         a.get("type", "Unknown"),
                "date":         a.get("start_date_local", "")[:10],
                "distance_km":  dist_km,
                "duration_min": dur_min,
                "elevation_m":  a.get("total_elevation_gain", 0),
                "avg_hr":       a.get("average_heartrate", 0),
            })

        return {
            "connected":           True,
            "activities_7d":       parsed,
            "total_distance_km":   round(total_distance_km, 1),
            "total_duration_min":  total_duration_min,
            "activity_count_7d":   len(parsed),
            "pulled_at":           datetime.now().isoformat()
        }
    except Exception as ex:
        return {"connected": True, "activities": [], "error": str(ex)}
