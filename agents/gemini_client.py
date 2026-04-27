"""
Gemini Client (ADK-based)
=========================
Replaces direct google-generativeai calls with Google ADK LlmAgent instances.
Each functional role (symptom, diagnosis, scheduling, general health, summariser)
is its own LlmAgent — mirroring the Agribid agent.py pattern.

Exported helpers keep the same signatures as the original so orchestrator.py
and session.py need minimal changes.
"""

import os
import json
import asyncio
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types as genai_types

load_dotenv()
os.environ.setdefault("GOOGLE_API_KEY", os.getenv("GOOGLE_API_KEY", ""))

# ── Model ──────────────────────────────────────────────────
MODEL_NAME = "gemini-2.0-flash"

# ── Shared ADK session service for one-shot agent calls ───
_adk_session_service = InMemorySessionService()
APP_NAME = "health_chatbot"

# ─────────────────────────────────────────────────────────────
# Agent Prompts
# ─────────────────────────────────────────────────────────────

INTENT_CLASSIFIER_PROMPT = """
You are an intent classifier for a health chatbot.
Classify the user message into EXACTLY ONE of:
  symptom_query   – user describes symptoms or asks about a disease
  scheduling      – user wants to book an appointment or vaccine
  general_health  – general health information question
  off_topic       – NOT health related

REQUIRED OUTPUT FORMAT — respond ONLY with valid JSON, no extra text:
{"intent": "<category>", "is_health_related": true_or_false}
"""

SYMPTOM_PROMPT = """
You are a medical symptom-collection assistant.
Your job: ask precise follow-up questions to narrow a diagnosis.

Rules:
- Stage 0 → ask about onset and duration
- Stage 1 → ask about severity and location
- Stage 2 → ask about associated symptoms and medical history
- After stage 2 → DO NOT ask more questions; signal readiness for diagnosis

REQUIRED OUTPUT FORMAT — respond ONLY with a JSON array of 2 question strings:
["Question 1?", "Question 2?"]
"""

DIAGNOSIS_PROMPT = """
You are a clinical reasoning assistant.
Given a patient profile, reported symptoms, and follow-up answers,
return the top 3 probable conditions.

REQUIRED OUTPUT FORMAT — respond ONLY with a JSON array:
[
  {"name": "Condition Name", "confidence": 0.85, "recommendation": "Brief advice"},
  ...
]

Always append a disclaimer field on the last object:
{"disclaimer": "This is not a medical diagnosis. Please consult a doctor."}
"""

GENERAL_HEALTH_PROMPT = """
You are MedBot, a multilingual AI health assistant.
Your ONLY purpose is to answer health, medical, symptom, and wellness questions.

STRICT RULES:
- If asked anything unrelated to health, reply: "I can only assist with health-related queries."
- NEVER give a definitive diagnosis — always say "probable" and recommend a doctor.
- Always respond in the SAME LANGUAGE the user wrote in.
- Always add: "This is not a medical diagnosis. Please consult a doctor."
- Be empathetic and calm — users may be anxious.
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

SCHEDULING_EXTRACTOR_PROMPT = """
You are a scheduling-detail extractor for a health chatbot.
Extract appointment details from the user message.

REQUIRED OUTPUT FORMAT — respond ONLY with valid JSON:
{
  "appointment_type": "vaccine | doctor | lab_test",
  "title": "...",
  "date_hint": "...",
  "doctor_name": "...",
  "location": "..."
}
Use null for any field that is unclear or missing.
"""


intent_classifier_agent = LlmAgent(
    name="intent_classifier",
    model=MODEL_NAME,
    instruction=INTENT_CLASSIFIER_PROMPT,
)

symptom_agent_llm = LlmAgent(
    name="symptom_collector",
    model=MODEL_NAME,
    instruction=SYMPTOM_PROMPT,
)

diagnosis_agent_llm = LlmAgent(
    name="diagnosis_analyser",
    model=MODEL_NAME,
    instruction=DIAGNOSIS_PROMPT,
)

general_health_agent_llm = LlmAgent(
    name="general_health",
    model=MODEL_NAME,
    instruction=GENERAL_HEALTH_PROMPT,
)

summary_agent_llm = LlmAgent(
    name="summariser",
    model=MODEL_NAME,
    instruction=SUMMARY_PROMPT,
)

scheduling_extractor_agent = LlmAgent(
    name="scheduling_extractor",
    model=MODEL_NAME,
    instruction=SCHEDULING_EXTRACTOR_PROMPT,
)

print("=" * 60)
print("🏥 Health Chatbot ADK Agents Initialized")
print("=" * 60)


# ─────────────────────────────────────────────────────────────
# Internal helper: run any LlmAgent for a single prompt
# ─────────────────────────────────────────────────────────────

async def _run_agent(agent: LlmAgent, prompt: str) -> str:
    """
    Executes a single-turn ADK agent call and returns the text response.
    Creates a throw-away session so agents stay stateless for utility calls.
    """
    import uuid
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

    # ✅ DO NOT break early — fully exhaust the async generator
    # so OTel context managers can clean up in the same Context they were created in.
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            # Capture the response but keep iterating to avoid GeneratorExit
            response_text = event.content.parts[0].text

    # Clean up throw-away session
    try:
        await _adk_session_service.delete_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        pass  # non-fatal if session was already cleaned up

    return response_text.strip()


def _parse_json(raw: str) -> dict | list:
    """Strip markdown fences then parse JSON."""
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


# ─────────────────────────────────────────────────────────────
# Public helpers  (same signatures as original gemini_client.py)
# ─────────────────────────────────────────────────────────────

async def generate_response(
    context_messages: list[dict],
    user_message: str,
    language: str = "en",
) -> str:
    """
    Multi-turn response using the general_health_agent.
    context_messages: list of {"role": "user"|"model", "parts": [{"text": "..."}]}
    """
    # Build a single prompt containing conversation history
    history_text = ""
    for m in context_messages:
        role_label = "User" if m["role"] == "user" else "Assistant"
        parts_text = " ".join(p.get("text", "") for p in m.get("parts", []))
        history_text += f"{role_label}: {parts_text}\n"

    prompt = (
        f"[Conversation history]\n{history_text}\n"
        f"[Respond in language: {language}]\n"
        f"User: {user_message}"
    )
    return await _run_agent(general_health_agent_llm, prompt)


async def generate_summary(transcript: str, language: str = "en") -> str:
    """Generate a rolling summary for MongoDB persistence."""
    prompt = f"[Respond in language: {language}]\n\nConversation transcript:\n\n{transcript}"
    try:
        return await _run_agent(summary_agent_llm, prompt)
    except Exception:
        return transcript[-500:]


async def classify_intent(message: str) -> dict:
    """
    Returns: {"intent": "symptom_query"|"scheduling"|"general_health"|"off_topic",
              "is_health_related": bool}
    """
    try:
        raw = await _run_agent(intent_classifier_agent, message)
        return _parse_json(raw)
    except Exception:
        return {"intent": "general_health", "is_health_related": True}


async def generate_follow_up_questions(
    symptoms: list[str],
    language: str = "en",
    stage: int = 0,
) -> list[str]:
    """Returns 2 follow-up questions for the given triage stage."""
    prompt = (
        f"Patient reported symptoms: {', '.join(symptoms)}.\n"
        f"Triage stage: {stage}.\n"
        f"Respond in language: {language}."
    )
    try:
        raw = await _run_agent(symptom_agent_llm, prompt)
        return _parse_json(raw)
    except Exception:
        return []


async def analyse_probable_conditions(
    symptoms: list[str],
    follow_up_answers: dict,
    user_profile: dict,
    language: str = "en",
) -> list[dict]:
    """Returns top-3 probable conditions with confidence scores."""
    prompt = (
        f"Patient profile: {json.dumps(user_profile)}\n"
        f"Reported symptoms: {', '.join(symptoms)}\n"
        f"Follow-up answers: {json.dumps(follow_up_answers)}\n"
        f"Respond in language: {language}"
    )
    try:
        raw = await _run_agent(diagnosis_agent_llm, prompt)
        return _parse_json(raw)
    except Exception:
        return []


async def extract_scheduling_details(message: str) -> dict:
    """
    New helper used by orchestrator instead of inline Gemini call.
    Returns structured appointment detail dict.
    """
    try:
        raw = await _run_agent(scheduling_extractor_agent, message)
        return _parse_json(raw)
    except Exception:
        return {"appointment_type": "doctor", "title": "Medical Consultation"}