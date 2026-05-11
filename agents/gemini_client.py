"""
Gemini Client (ADK-based) — Lean 3-Agent Design
=================================================
Agents:
  1. intent_classifier  — routes every message to the right handler
  2. health_agent       — handles conversation, symptom staging, and diagnosis
  3. summariser         — called only at session end for MongoDB persistence

Scheduling detail extraction is handled by a plain structured prompt
inside extract_scheduling_details() — no separate agent needed.

Public API is identical to the original so orchestrator.py / session.py
need no changes.
"""

import os
import json
import uuid
import asyncio
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types as genai_types

load_dotenv()
os.environ.setdefault("GOOGLE_API_KEY", os.getenv("GOOGLE_API_KEY", ""))


MODEL_NAME = "gemini-2.0-flash"


_adk_session_service = InMemorySessionService()
APP_NAME = "health_chatbot"


# ──────────────────────────────────────────────────────────────────────────────
# Agent Prompts
# ──────────────────────────────────────────────────────────────────────────────

# In INTENT_CLASSIFIER_PROMPT — make language field explicit in output:
INTENT_CLASSIFIER_PROMPT = """
You are an intent classifier for a health chatbot.
Classify the user message into EXACTLY ONE of:
  symptom_query   – user describes symptoms or asks about a disease
  scheduling      – user wants to book an appointment or vaccine
  general_health  – general health information question
  off_topic       – NOT health related

Detect the language of the user message.

REQUIRED OUTPUT FORMAT — respond ONLY with valid JSON, no extra text:
{"intent": "<category>", "is_health_related": true_or_false, "language": "<ISO 639-1 code e.g. en, hi, mr, es>"}
"""

HEALTH_AGENT_PROMPT = """
You are MedBot, a multilingual clinical AI health assistant.
You handle three responsibilities depending on context provided to you:

── GENERAL HEALTH ──────────────────────────────────────────────────────────────
Answer health, medical, symptom, and wellness questions empathetically.
Never give a definitive diagnosis — always say "probable" and recommend a doctor.
If asked anything unrelated to health reply: "I can only assist with health-related queries."
Always add: "This is not a medical diagnosis. Please consult a doctor."

── SYMPTOM COLLECTION (stage 0, 1, 2) ──────────────────────────────────────────
When the context says [MODE: symptom_collection, STAGE: N]:
  Stage 0 → ask about onset and duration
  Stage 1 → ask about severity and location
  Stage 2 → ask about associated symptoms and medical history
After collecting stage 2 answers, signal completion with [READY_FOR_DIAGNOSIS].

REQUIRED OUTPUT FORMAT for symptom collection — respond ONLY with a JSON array:
["Question 1?", "Question 2?"]

── DIAGNOSIS ────────────────────────────────────────────────────────────────────
When the context says [MODE: diagnosis]:
Given the patient profile, symptoms, and follow-up answers, return top 3 probable conditions.

REQUIRED OUTPUT FORMAT for diagnosis — respond ONLY with a JSON array:
[
  {"name": "Condition Name", "confidence": 0.85, "recommendation": "Brief advice"},
  {"name": "Condition Name", "confidence": 0.70, "recommendation": "Brief advice"},
  {"name": "Condition Name", "confidence": 0.55, "recommendation": "Brief advice"},
  {"disclaimer": "This is not a medical diagnosis. Please consult a doctor."}
]

── GENERAL RULES ────────────────────────────────────────────────────────────────
Always respond in the Same Language.
Be calm and empathetic — users may be anxious.
Also Detect the Langugae and Reply the User in in Same Language Strictly
"""

SUMMARY_PROMPT = """
You are a medical conversation summariser.
Given a conversation transcript between a health assistant and a patient,
produce a concise clinical summary (max 300 words) covering:
  - Main symptoms reported
  - Follow-up answers given
  - Probable conditions discussed
  - Appointments scheduled
  - Key health context (age, pre-existing conditions if mentioned)

Write in third person. Be factual. Omit small talk.
"""


# ──────────────────────────────────────────────────────────────────────────────
# Agent instances  (3 total)
# ──────────────────────────────────────────────────────────────────────────────

intent_classifier_agent = LlmAgent(
    name="intent_classifier",
    model=MODEL_NAME,
    instruction=INTENT_CLASSIFIER_PROMPT,
)

health_agent_llm = LlmAgent(
    name="health_agent",
    model=MODEL_NAME,
    instruction=HEALTH_AGENT_PROMPT,
)

summary_agent_llm = LlmAgent(
    name="summariser",
    model=MODEL_NAME,
    instruction=SUMMARY_PROMPT,
)

print("=" * 60)
print("🏥 Health Chatbot ADK Agents Initialized (lean 3-agent)")
print("=" * 60)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helper: single-turn stateless agent call
# ──────────────────────────────────────────────────────────────────────────────

async def _run_agent(agent: LlmAgent, prompt: str) -> str:
    """
    Executes a single-turn ADK agent call and returns the text response.
    Uses a throw-away session so every call is stateless.
    """
    session_id = f"util-{uuid.uuid4().hex[:8]}"
    user_id = "system"

    _adk_session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=_adk_session_service,
    )

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=prompt)],
    )

    response_text = ""

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            response_text = event.content.parts[0].text

    try:
        await _adk_session_service.delete_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        pass  # non-fatal

    return response_text.strip()


def _parse_json(raw: str) -> dict | list:
    """Strip markdown fences then parse JSON."""
    cleaned = (
        raw.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    return json.loads(cleaned)


def _build_history_text(context_messages: list[dict]) -> str:
    """Flatten ADK-style message dicts into a labelled history string."""
    lines = []
    for m in context_messages:
        role_label = "User" if m["role"] == "user" else "Assistant"
        parts_text = " ".join(p.get("text", "") for p in m.get("parts", []))
        lines.append(f"{role_label}: {parts_text}")
    return "\n".join(lines)


async def classify_intent(message: str) -> dict:
    """
    Returns:
        {"intent": "symptom_query"|"scheduling"|"general_health"|"off_topic",
         "is_health_related": bool}
    """
    try:
        raw = await _run_agent(intent_classifier_agent, message)
        return _parse_json(raw)
    except Exception:
        return {"intent": "general_health", "is_health_related": True}


# async def generate_response(
#     context_messages: list[dict],
#     user_message: str,
    
# ) -> str:
#     """
#     Multi-turn general health response using the unified health_agent.
#     context_messages: list of {"role": "user"|"model", "parts": [{"text": "..."}]}
#     """
#     history_text = _build_history_text(context_messages)
#     prompt = (
#         "[MODE: general_health]\n"
#         f"[Conversation history]\n{history_text}\n\n"
#         f"User: {user_message}"
#     )
#     return await _run_agent(health_agent_llm, prompt)

async def generate_response(
    context_messages: list[dict],
    user_message: str,
    language: str = "en",          
) -> str:
    history_text = _build_history_text(context_messages)
    prompt = (
        f"[LANGUAGE: Always respond in language code '{language}'. Do NOT switch languages.]\n"
        "[MODE: general_health]\n"
        f"[Conversation history]\n{history_text}\n\n"
        f"User: {user_message}"
    )
    return await _run_agent(health_agent_llm, prompt)

# async def generate_follow_up_questions(
#     symptoms: list[str],
#     stage: int = 0,
# ) -> list[str]:
#     """
#     Returns 2 follow-up questions for the given triage stage.
#     Uses health_agent in symptom_collection mode — no separate agent needed.
#     """
#     prompt = (
#         f"[MODE: symptom_collection, STAGE: {stage}]\n"
#         f"Patient reported symptoms: {', '.join(symptoms)}."
#     )
#     try:
#         raw = await _run_agent(health_agent_llm, prompt)
#         return _parse_json(raw)
#     except Exception:
#         return []

async def generate_follow_up_questions(
    symptoms: list[str],
    stage: int = 0,
    language: str = "en",          # ← ADD THIS
) -> list[str]:
    prompt = (
        f"[LANGUAGE: Always respond in language code '{language}'. Do NOT switch languages.]\n"
        f"[MODE: symptom_collection, STAGE: {stage}]\n"
        f"Patient reported symptoms: {', '.join(symptoms)}."
    )
    try:
        raw = await _run_agent(health_agent_llm, prompt)
        return _parse_json(raw)
    except Exception:
        return []
    
# async def analyse_probable_conditions(
#     symptoms: list[str],
#     follow_up_answers: dict,
#     user_profile: dict,
# ) -> list[dict]:
#     """
#     Returns top-3 probable conditions with confidence scores.
#     Uses health_agent in diagnosis mode — no separate agent needed.
#     """
#     prompt = (
#         "[MODE: diagnosis]\n"
#         f"Patient profile: {json.dumps(user_profile)}\n"
#         f"Reported symptoms: {', '.join(symptoms)}\n"
#         f"Follow-up answers: {json.dumps(follow_up_answers)}"
#     )
#     try:
#         raw = await _run_agent(health_agent_llm, prompt)
#         return _parse_json(raw)
#     except Exception:
#         return []

async def analyse_probable_conditions(
    symptoms: list[str],
    follow_up_answers: dict,
    user_profile: dict,
    language: str = "en",          # ← ADD THIS
) -> list[dict]:
    prompt = (
        f"[LANGUAGE: Always respond in language code '{language}'. Do NOT switch languages.]\n"
        "[MODE: diagnosis]\n"
        f"Patient profile: {json.dumps(user_profile)}\n"
        f"Reported symptoms: {', '.join(symptoms)}\n"
        f"Follow-up answers: {json.dumps(follow_up_answers)}"
    )
    try:
        raw = await _run_agent(health_agent_llm, prompt)
        return _parse_json(raw)
    except Exception:
        return []
async def generate_summary(transcript: str) -> str:
    """
    Generate a rolling summary for MongoDB persistence.
    Called only at session end — keeps summariser separate so its
    clinical tone doesn't bleed into the conversational health_agent.
    """
    prompt = (
        f"Conversation transcript:\n\n{transcript}"
    )
    try:
        return await _run_agent(summary_agent_llm, prompt)
    except Exception:
        return transcript[-500:]


# async def extract_scheduling_details(message: str) -> dict:
#     """
#     Extract appointment details from a user message.

#     Uses a plain structured prompt against health_agent rather than a
#     dedicated scheduling agent — the task is simple NER, not a reasoning
#     workflow, so a full LlmAgent is overkill.
#     """
#     prompt = (
#         "[MODE: scheduling_extraction]\n"
#         "Extract appointment details from the message below.\n"
#         "Respond ONLY with valid JSON — no extra text:\n"
#         "{\n"
#         '  "appointment_type": "vaccine | doctor | lab_test",\n'
#         '  "title": "...",\n'
#         '  "date_hint": "...",\n'
#         '  "doctor_name": "...",\n'
#         '  "location": "..."\n'
#         "}\n"
#         "Use null for any field that is unclear or missing.\n\n"
#         f"Message: {message}"
#     )
#     try:
#         raw = await _run_agent(health_agent_llm, prompt)
#         return _parse_json(raw)
#     except Exception:
#         return {"appointment_type": "doctor", "title": "Medical Consultation"}
async def extract_scheduling_details(message: str, user_timezone_offset: str = "+05:30") -> dict:
    """
    Extract appointment details from a user message.
    LLM resolves natural language dates to ISO 8601 UTC strings.
    
    Args:
        message: User's message containing appointment details
        user_timezone_offset: UTC offset string e.g. "+05:30" for IST, "+00:00" for UTC
    """
    from datetime import datetime, timezone, timedelta

    # Parse the timezone offset string into a timezone object
    try:
        sign = 1 if user_timezone_offset[0] != "-" else -1
        offset_str = user_timezone_offset.lstrip("+-")
        hours, minutes = map(int, offset_str.split(":"))
        user_tz = timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
    except Exception:
        user_tz = timezone(timedelta(hours=5, minutes=30))  # fallback to IST

    now_local = datetime.now(tz=user_tz)
    now_utc   = now_local.astimezone(timezone.utc)

    prompt = (
        "[MODE: scheduling_extraction]\n"
        f"Current local datetime : {now_local.strftime('%Y-%m-%dT%H:%M:%S')} (UTC{user_timezone_offset})\n"
        f"Current UTC datetime   : {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}\n\n"
        "Rules for date resolution:\n"
        "  1. Treat all user-mentioned times as LOCAL time (UTC" + user_timezone_offset + ").\n"
        "  2. Convert the resolved local datetime to UTC before writing date_hint.\n"
        "  3. date_hint MUST be a valid ISO 8601 UTC string, e.g. '2026-05-10T09:00:00Z'.\n"
        "  4. If only a date is given with no time, default to 09:00 local time → convert to UTC.\n"
        "  5. If the date is ambiguous or missing entirely, use null for date_hint.\n"
        "  6. 'tomorrow' means " + (now_local + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d') + ".\n"
        "  7. 'next week' means the same weekday 7 days from today.\n\n"
        "Extract appointment details from the message below.\n"
        "Respond ONLY with valid JSON — no extra text, no markdown fences:\n"
        "{\n"
        '  "appointment_type": "doctor" | "vaccine" | "lab_test",\n'
        '  "title": "short descriptive title of the appointment",\n'
        '  "date_hint": "2026-05-10T03:30:00Z",\n'
        '  "doctor_name": "doctor name if mentioned or null",\n'
        '  "location": "location if mentioned or null"\n'
        "}\n\n"
        f"Message: {message}"
    )

    try:
        raw = await _run_agent(health_agent_llm, prompt)
        details = _parse_json(raw)

        # Validate and normalise date_hint
        if details.get("date_hint"):
            try:
                dt = datetime.fromisoformat(details["date_hint"].replace("Z", "+00:00"))
                details["date_hint"] = dt.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                details["date_hint"] = None

        # Normalise nulls from LLM
        for field in ("doctor_name", "location", "date_hint"):
            if details.get(field) in (None, "null", "none", "", "None"):
                details[field] = None

        # Fallback appointment_type
        valid_types = {"doctor", "vaccine", "lab_test"}
        if details.get("appointment_type") not in valid_types:
            details["appointment_type"] = "doctor"

        return details

    except Exception:
        return {
            "appointment_type": "doctor",
            "title":            "Medical Consultation",
            "date_hint":        None,
            "doctor_name":      None,
            "location":         None,
        }