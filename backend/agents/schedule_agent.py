"""
OMNIX V2 — Schedule Agent
Takes raw user text input + profile and generates a complete,
time-blocked, personalized schedule using LLM.
Falls back to smart local generation if no API key.
"""
import json
import os
import time
import re
import httpx
from datetime import datetime, timedelta

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

SCHEDULE_SYSTEM_PROMPT = """You are OMNIX's Schedule Agent — a world-class life optimizer.

Your job: Take a user's raw description of their day + their personal profile, and return a PERFECT time-blocked schedule as a JSON array.

Rules:
1. Return ONLY a valid JSON array of schedule blocks. No preamble. No markdown. No explanation.
2. Each block MUST have ALL these fields:
   - time: "HH:MM" (24-hour format)
   - title: string (specific and action-oriented)
   - emoji: single emoji
   - category: one of [focus, work, health, rest, learn, social, deep]
   - duration: string like "45 min" or "1.5 hrs"
   - location: string (where they'll be)
   - reasoning: string (WHY this slot at this time — cite science/psychology where possible)
   - tasks: array of 2-4 specific actionable sub-tasks
   - detail: string (1-2 sentence elaboration)
3. Generate 6-9 blocks covering the full day
4. Time blocks must be REALISTIC — respect sleep time, meals, travel
5. Schedule around PEAK productivity times from the profile
6. If exam/deadline mentioned — make that the TOP priority
7. Include health, breaks, and recovery — peak performance requires it
8. Be SPECIFIC, not generic. "Review Chapter 7 integration by parts" not "Study math"
9. The tone of reasoning should match the user's preferred communication style"""

SCHEDULE_USER_TEMPLATE = """User's description of their day:
"{user_input}"

User profile:
- Name: {name}
- Role: {role}
- Wakeup time: {wakeup_time}
- Peak productivity: {peak_time}
- Average sleep: {sleep_hours}h
- Biggest challenge: {challenge}
- Exercise habit: {exercise}
- Communication tone: {tone}
- Current context/goals: {context}

Generate a complete, optimized time-blocked schedule for today. Return ONLY the JSON array."""


def _call_gemini(user_input: str, profile: dict) -> list | None:
    if not GEMINI_API_KEY:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

    user_msg = SCHEDULE_USER_TEMPLATE.format(
        user_input=user_input,
        name=profile.get("name", "User"),
        role=profile.get("role", "student"),
        wakeup_time=profile.get("wakeup_time", "07:00"),
        peak_time=profile.get("peak_time", "morning"),
        sleep_hours=profile.get("sleep_hours", "7"),
        challenge=profile.get("challenge", "focus"),
        exercise=profile.get("exercise", "3-4x"),
        tone=profile.get("tone", "direct"),
        context=profile.get("context", "General productivity")
    )

    payload = {
        "system_instruction": {"parts": [{"text": SCHEDULE_SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_msg}]}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json"
        }
    }

    try:
        with httpx.Client(timeout=18.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(text)
            if isinstance(result, list):
                return result
            # Sometimes Gemini wraps in object
            for v in result.values():
                if isinstance(v, list):
                    return v
    except Exception as e:
        print(f"[SCHEDULE] Gemini failed: {e}")
    return None


def _call_openai(user_input: str, profile: dict) -> list | None:
    if not OPENAI_API_KEY:
        return None

    user_msg = SCHEDULE_USER_TEMPLATE.format(
        user_input=user_input,
        name=profile.get("name", "User"),
        role=profile.get("role", "student"),
        wakeup_time=profile.get("wakeup_time", "07:00"),
        peak_time=profile.get("peak_time", "morning"),
        sleep_hours=profile.get("sleep_hours", "7"),
        challenge=profile.get("challenge", "focus"),
        exercise=profile.get("exercise", "3-4x"),
        tone=profile.get("tone", "direct"),
        context=profile.get("context", "General productivity")
    )

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SCHEDULE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"}
    }

    try:
        with httpx.Client(timeout=18.0) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            for v in parsed.values():
                if isinstance(v, list):
                    return v
    except Exception as e:
        print(f"[SCHEDULE] OpenAI failed: {e}")
    return None


def _parse_time_from_input(user_input: str) -> dict:
    """Extract time hints from user's text."""
    hints = {}
    lower = user_input.lower()

    # Extract mentioned times (e.g. "exam at 10am", "meeting at 2:30pm")
    time_pattern = r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))'
    times_found = re.findall(time_pattern, lower)

    # Detect keywords
    hints["has_exam"] = any(w in lower for w in ["exam", "test", "paper", "quiz"])
    hints["has_gym"] = any(w in lower for w in ["gym", "workout", "exercise", "training"])
    hints["skip_gym"] = any(w in lower for w in ["skip gym", "skip the gym", "no gym", "skipping gym"])
    hints["has_meeting"] = any(w in lower for w in ["meeting", "call", "standup", "sync"])
    hints["has_deadline"] = any(w in lower for w in ["deadline", "submit", "due", "assignment"])
    hints["low_energy"] = any(w in lower for w in ["tired", "exhausted", "sleepy", "low energy", "bad sleep", "slept badly"])
    hints["has_study_group"] = any(w in lower for w in ["study group", "group study"])
    hints["times_mentioned"] = times_found

    return hints


def _local_schedule(user_input: str, profile: dict) -> list:
    """
    Smart local schedule generation — no API needed.
    Produces a realistic, personalized schedule.
    """
    hints = _parse_time_from_input(user_input)
    wakeup = profile.get("wakeup_time", "07:00")
    peak = profile.get("peak_time", "morning")
    challenge = profile.get("challenge", "focus")
    tone = profile.get("tone", "direct")
    context = profile.get("context", "")
    exercise = profile.get("exercise", "3-4x")
    sleep_hrs = float(profile.get("sleep_hours", 7))

    try:
        wh, wm = [int(x) for x in wakeup.split(":")]
    except Exception:
        wh, wm = 7, 0

    blocks = []

    def t(offset_30min_slots: int) -> str:
        total_mins = wh * 60 + wm + offset_30min_slots * 30
        h = (total_mins // 60) % 24
        m = total_mins % 60
        return f"{h:02d}:{m:02d}"

    # ── Block 0: Morning routine ─────────────────
    tone_reason = {
        "direct": "Non-negotiable. Skipping this tanks your morning.",
        "friendly": "Starting with intention sets the tone for everything that follows!",
        "coach": "Champions build systems, not moods. This is your system.",
        "minimal": "15 minutes. Do it."
    }.get(tone, "Critical for cognitive priming.")

    blocks.append({
        "time": t(0),
        "title": "Morning Routine",
        "emoji": "🌅",
        "category": "health",
        "duration": "30 min",
        "location": "Home",
        "reasoning": tone_reason,
        "detail": "The first 30 minutes determine the quality of your entire day. Non-negotiable.",
        "tasks": [
            "Drink 500ml water immediately on waking",
            "5-minute stretching or breathing exercise",
            "Review your 3 top priorities for today",
            "No phone for the first 20 minutes"
        ]
    })

    # ── Block 1: Breakfast ───────────────────────
    blocks.append({
        "time": t(1),
        "title": "Breakfast + Brain Fuel",
        "emoji": "🍳",
        "category": "health",
        "duration": "20 min",
        "location": "Home",
        "reasoning": "Skipping breakfast with high cognitive load reduces working memory by 20%. High-protein slows glucose absorption, sustaining focus for 3-4 hours.",
        "detail": "Protein + complex carbs. Avoid sugary cereals that cause a crash in 90 minutes.",
        "tasks": [
            "Eggs / protein-rich meal",
            "Eat at the table — no screens",
            "Coffee or tea (not immediately — wait 90 min after waking for cortisol to drop)"
        ]
    })

    # ── Block 2: Priority block (exam / work / deep work) ──
    if hints["has_exam"]:
        blocks.append({
            "time": t(2),
            "title": "Exam Prep — Targeted Revision",
            "emoji": "📚",
            "category": "focus",
            "duration": "75 min",
            "location": "Desk / Library",
            "reasoning": "Spaced repetition outperforms re-reading by 50% for retention. 75-minute blocks align with ultradian rhythm. Focus ONLY on weak areas — not what you already know.",
            "detail": "This is your most important block of the day. Everything else supports this.",
            "tasks": [
                "Quick formula/concept sheet review (10 min)",
                "Weak topic drill — practice problems only (35 min)",
                "Past paper questions — high yield, timed (25 min)",
                "Stop 5 min before exam window to calm nervous system"
            ]
        })
    elif hints["has_deadline"]:
        blocks.append({
            "time": t(2),
            "title": "Deep Work — Deadline Sprint",
            "emoji": "🔥",
            "category": "deep",
            "duration": "90 min",
            "location": "Desk",
            "reasoning": "Deadline pressure activates the prefrontal cortex. Capture this state with a complete distraction block. Single-tasking on the deliverable.",
            "detail": "This window is sacred. Tell people you're unavailable.",
            "tasks": [
                "Open ONLY the files you need — close everything else",
                "Phone in another room or on airplane mode",
                "Work in 45-min Pomodoro blocks with 10-min breaks",
                "Target: complete the submission-ready draft"
            ]
        })
    else:
        peak_label = {
            "early_morning": "Your early morning peak — rare and powerful.",
            "morning": "Morning peak productivity. Best time for your hardest thinking.",
            "afternoon": "Afternoon focus window — body temperature peaks, reaction time best.",
            "evening": "Your peak is evening — lean in. Do the hard work now.",
            "night": "Night owl mode active. World is quiet. Execute."
        }.get(peak, "Your peak focus window.")

        blocks.append({
            "time": t(2),
            "title": "Deep Work Block #1",
            "emoji": "💻",
            "category": "deep",
            "duration": "90 min",
            "location": "Desk",
            "reasoning": f"{peak_label} {'Procrastination? Do the hardest task first — activation energy drops after 5 minutes.' if challenge == 'procrastination' else 'Block all inputs. Execute.'}",
            "detail": "Your highest-value work goes here. Protect this block like an exam.",
            "tasks": [
                "Most important task: identify it before sitting down",
                "All notifications off",
                "45-min focused sprint → 10-min genuine break → repeat",
                "Track what you completed, not what you planned"
            ]
        })

    # ── Block 3: Exam itself (if mentioned) ─────
    if hints["has_exam"]:
        blocks.append({
            "time": t(5),  # ~2.5h after wakeup — approximate exam time
            "title": "EXAM",
            "emoji": "🎯",
            "category": "deep",
            "duration": "2 hrs",
            "location": "Exam Hall",
            "reasoning": "You've prepared. Anxiety is just excitement. Trust the work you've put in.",
            "detail": "Execution mode. Strategy over panic.",
            "tasks": [
                "Read ALL questions before writing a single word",
                "Attempt easiest/highest-mark questions first",
                "Time-box each section — don't get stuck",
                "Leave 10 min at end for review"
            ]
        })

    # ── Block 4: Lunch + recovery ────────────────
    blocks.append({
        "time": t(7),
        "title": "Lunch + Recovery",
        "emoji": "🍱",
        "category": "rest",
        "duration": "45 min",
        "location": "—",
        "reasoning": "Mental recovery is not laziness — it's performance maintenance. A 10-20 min walk after lunch reduces post-lunch dip by 40% and improves afternoon focus.",
        "detail": "Eat away from your desk. Give your brain a real break.",
        "tasks": [
            "Eat a balanced meal — avoid heavy carbs that cause afternoon crash",
            "10-minute walk after eating",
            "No work thoughts during this window",
            "Hydrate — aim for 250ml water"
        ]
    })

    # ── Block 5: Gym (if applicable) ─────────────
    if hints["has_gym"] and not hints["skip_gym"] and exercise in ["daily", "3-4x"]:
        blocks.append({
            "time": t(10),
            "title": "Gym / Training",
            "emoji": "🏋️",
            "category": "health",
            "duration": "60 min",
            "location": "Gym",
            "reasoning": "Afternoon workouts (3-6pm) align with peak body temperature and testosterone levels — 15-20% strength advantage vs morning. BDNF released post-workout boosts learning retention.",
            "detail": "Train hard, recover smart. Post-workout is your best learning window.",
            "tasks": [
                "5-min dynamic warm-up",
                "Main training block",
                "Cool-down + stretching (10 min)",
                "Protein within 30-45 min post-workout"
            ]
        })
    elif hints["skip_gym"]:
        blocks.append({
            "time": t(10),
            "title": "Light Movement (Gym Rescheduled)",
            "emoji": "🚶",
            "category": "health",
            "duration": "20 min",
            "location": "Outside",
            "reasoning": "Gym skipped — smart call given today's load. Even 20 min of walking releases BDNF and resets cortisol. Non-negotiable minimum.",
            "detail": "No workout ≠ no movement. Walk outside.",
            "tasks": [
                "20-minute brisk walk",
                "No phone — let your mind decompress",
                "Reschedule gym for tomorrow in OMNIX"
            ]
        })

    # ── Block 6: Study group / meetings ──────────
    if hints["has_study_group"]:
        blocks.append({
            "time": t(12),
            "title": "Study Group Session",
            "emoji": "👥",
            "category": "social",
            "duration": "90 min",
            "location": "Library / Online",
            "reasoning": "Teaching concepts to others improves your own retention by up to 90% (Protégé effect). Collaborative learning surfaces blind spots solo study misses.",
            "detail": "Come prepared. Teach, don't just receive.",
            "tasks": [
                "Prepare 2-3 questions to ask the group",
                "Explain at least one concept to someone else",
                "Take notes on anything you got wrong",
                "Leave on time — protect your evening block"
            ]
        })
    elif hints["has_meeting"]:
        blocks.append({
            "time": t(12),
            "title": "Meetings / Collaboration",
            "emoji": "🤝",
            "category": "work",
            "duration": "60 min",
            "location": "Office / Online",
            "reasoning": "Batching meetings into a single window eliminates context-switching overhead — saves 45+ min vs scattered meetings throughout the day.",
            "detail": "Batched. Bounded. Purposeful.",
            "tasks": [
                "Prep talking points 5 minutes before each",
                "Send action items immediately after — don't batch them",
                "Time-box each meeting — set a timer",
                "Default to 25-min meetings, not 60-min"
            ]
        })

    # ── Block 7: Second work block ───────────────
    blocks.append({
        "time": t(14),
        "title": "Deep Work Block #2",
        "emoji": "⚡",
        "category": "focus",
        "duration": "75 min",
        "location": "Desk",
        "reasoning": f"Second wind. Use this for your second-priority work. {'Low energy? Break tasks into 10-min micro-chunks — momentum builds itself.' if hints['low_energy'] else 'You have fuel left. Use it.'}",
        "detail": "Tackle the second most important thing on your list.",
        "tasks": [
            "Second priority task — defined before sitting down",
            "Review and respond to critical messages (10 min max)",
            "Output over perfection — ship something",
            "Save admin/emails for the last 15 min"
        ]
    })

    # ── Block 8: Evening wind-down + planning ────
    bedtime_h = (wh + int(sleep_hrs)) % 24
    wind_down_h = (bedtime_h - 1) % 24
    wind_down_time = f"{wind_down_h:02d}:00"

    blocks.append({
        "time": wind_down_time,
        "title": "Evening Review + Tomorrow's Plan",
        "emoji": "📋",
        "category": "learn",
        "duration": "20 min",
        "location": "Home",
        "reasoning": "Daily review is the #1 habit of high-performers. 3 min of reflection consolidates the day. Pre-committing tomorrow's top 3 tasks reduces morning decision fatigue.",
        "detail": "Close the loop on today. Set up tomorrow's win.",
        "tasks": [
            "Write 3 things that went well today",
            "Identify 1 thing to improve tomorrow",
            "Set tomorrow's top 3 priorities in OMNIX",
            "Log today's OMNIX loop results"
        ]
    })

    # ── Block 9: Sleep ───────────────────────────
    sleep_time = f"{bedtime_h:02d}:00"
    blocks.append({
        "time": sleep_time,
        "title": f"Sleep — {sleep_hrs}h Target",
        "emoji": "😴",
        "category": "rest",
        "duration": f"{sleep_hrs}h",
        "location": "Bedroom",
        "reasoning": f"Sleep is when your brain consolidates everything learned today. {sleep_hrs}h is your target. Every hour under 7h reduces next-day cognitive performance by 10%. Non-negotiable.",
        "detail": "This is not rest. This is performance recovery. Protect it.",
        "tasks": [
            "No screens 30 min before — blue light suppresses melatonin",
            "Room temperature: 18-20°C for optimal sleep",
            "Tomorrow's top 3 already set in OMNIX — no rumination needed",
            "Phone on DND, face-down"
        ]
    })

    return blocks


def generate(user_input: str, profile: dict) -> tuple[list, str, float]:
    """
    Main schedule generation function.
    Returns: (schedule_blocks, provider_used, latency_seconds)
    """
    start = time.time()

    # Try Gemini
    result = _call_gemini(user_input, profile)
    if result and len(result) >= 4:
        return result, "gemini-1.5-flash", round(time.time() - start, 3)

    # Try OpenAI
    result = _call_openai(user_input, profile)
    if result and len(result) >= 4:
        return result, "gpt-4o-mini", round(time.time() - start, 3)

    # Local generation
    result = _local_schedule(user_input, profile)
    return result, "local-optimizer", round(time.time() - start, 3)
