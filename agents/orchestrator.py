"""
ADK Orchestrator
================
Routes every user turn to the right ADK-backed agent.

Sub-agents:
  SymptomAgent       → follow-up questions via ADK symptom_collector agent
  DiagnosisAgent     → probable conditions via ADK diagnosis_analyser agent
  SchedulingAgent    → appointment creation via ADK scheduling_extractor agent
  GeneralHealthAgent → open health Q&A via ADK general_health agent

All heavy LLM lifting is delegated to gemini_client.py helpers, which
internally run Google ADK LlmAgent instances — matching the Agribid pattern.
"""

from agents.gemini_client import (
    classify_intent,
    generate_response,
    generate_follow_up_questions,
    analyse_probable_conditions,
    extract_scheduling_details,        # new ADK-based helper
)
from session import session_service, InMemorySession
from services.appointment_service import AppointmentService
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Sub-Agents
# ─────────────────────────────────────────────

class SymptomAgent:
    """
    Collects symptoms via follow-up questions.
    Triggers DiagnosisAgent once 3 triage stages are complete.
    """

    async def run(self, session: InMemorySession, user_message: str) -> dict:
        state = session.state
        ctx   = state.setdefault("symptom_context", {})
        symptoms = ctx.setdefault("reported_symptoms", [])
        answers  = ctx.setdefault("follow_up_answers", {})
        stage    = state.get("triage_stage", 0)

        if stage == 0:
            symptoms.append(user_message)
            ctx["reported_symptoms"] = symptoms

        if stage > 0:
            answers[f"stage_{stage}"] = user_message
            ctx["follow_up_answers"] = answers

        follow_ups: list[str] = []
        probable_conditions: list[dict] = []

        if stage < 3:
            # ADK symptom_collector agent generates follow-up questions
            follow_ups = await generate_follow_up_questions(
                symptoms, session.language, stage
            )
            reply = (
                f"{follow_ups[0]}\n\n{follow_ups[1]}"
                if len(follow_ups) >= 2
                else (follow_ups[0] if follow_ups else "Can you describe your symptoms more?")
            )
            state["triage_stage"] = stage + 1
        else:
            # ADK diagnosis_analyser agent produces conditions
            user_profile = {
                "age": state.get("user_age"),
                "conditions": state.get("existing_conditions", []),
            }
            probable_conditions = await analyse_probable_conditions(
                symptoms, answers, user_profile, session.language
            )
            conditions_text = "\n".join(
                f"- {c['name']} ({int(c['confidence'] * 100)}%): {c['recommendation']}"
                for c in probable_conditions
                if "name" in c
            )
            reply = (
                f"Based on what you've described, here are probable conditions:\n\n"
                f"{conditions_text}\n\n"
                f"⚠️ This is not a medical diagnosis. Please consult a qualified doctor."
            )
            state["triage_stage"] = 0
            ctx["last_conditions"] = probable_conditions

        await session_service.update_state(session.session_id, state)

        return {
            "reply": reply,
            "intent": "symptom_query",
            "follow_up_questions": follow_ups,
            "probable_conditions": probable_conditions,
        }


class SchedulingAgent:
    """
    Extracts appointment intent via the ADK scheduling_extractor agent
    and creates a booking through AppointmentService.
    """

    def __init__(self):
        self._appt_service = AppointmentService()

    async def run(self, session: InMemorySession, user_message: str) -> dict:
        # ADK scheduling_extractor agent parses natural language → structured dict
        details = await extract_scheduling_details(user_message)

        missing = [k for k, v in details.items() if v is None and k != "doctor_name"]
        if missing or not details.get("date_hint"):
            return {
                "reply": (
                    "I'd be happy to schedule that! "
                    "Could you please provide the preferred date and time?"
                ),
                "intent": "scheduling",
                "appointment_created": None,
            }

        appt = await self._appt_service.create_appointment(
            user_id=session.user_id,
            appointment_type=details.get("appointment_type", "doctor"),
            title=details.get("title", "Appointment"),
            date_hint=details.get("date_hint", ""),
            doctor_name=details.get("doctor_name", ""),
            location=details.get("location", ""),
        )

        reply = (
            f"✅ Appointment scheduled!\n"
            f"📅 {appt['title']} on {appt['scheduled_at']}\n"
            f"📍 {appt.get('location', 'TBD')}\n"
            f"🔖 Reference: {appt['reference']}\n\n"
            f"You'll receive an SMS reminder before your appointment."
        )

        return {
            "reply": reply,
            "intent": "scheduling",
            "appointment_created": appt,
        }


class GeneralHealthAgent:
    """
    Handles open health Q&A via the ADK general_health agent.
    Passes full conversation context so the model stays coherent.
    """

    async def run(self, session: InMemorySession, user_message: str) -> dict:
        context = await session_service.build_gemini_context(session.session_id)
        reply = await generate_response(context, user_message, session.language)
        return {
            "reply": reply,
            "intent": "general_health",
            "follow_up_questions": [],
            "probable_conditions": [],
        }


# ─────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────

class OrchestratorAgent:
    """
    Entry point for every user message.

    Flow:
      1. Get / create hybrid session
      2. Classify intent  (ADK intent_classifier agent — fast Flash call)
      3. Health guardrail check
      4. Persist user message
      5. Route → SymptomAgent | SchedulingAgent | GeneralHealthAgent
      6. Persist assistant reply
      7. Return result dict to chat router
    """

    # Off-topic reply localised to supported languages
    _OFF_TOPIC: dict[str, str] = {
        "en": "I can only assist with health-related queries. How can I help with your health today?",
        "hi": "मैं केवल स्वास्थ्य संबंधी प्रश्नों में मदद कर सकता हूँ।",
        "mr": "मी फक्त आरोग्याशी संबंधित प्रश्नांना उत्तर देऊ शकतो.",
        "es": "Solo puedo ayudar con consultas relacionadas con la salud.",
        "fr": "Je ne peux aider qu'avec des questions liées à la santé.",
        "ar": "يمكنني فقط المساعدة في الاستفسارات المتعلقة بالصحة.",
        "ta": "நான் உடல்நலம் தொடர்பான கேள்விகளுக்கு மட்டுமே உதவ முடியும்.",
    }

    def __init__(self):
        self._symptom_agent   = SymptomAgent()
        self._scheduling_agent = SchedulingAgent()
        self._general_agent   = GeneralHealthAgent()

    async def process(
        self,
        user_id: str,
        message: str,
        session_id: str | None = None,
        language: str = "en",
    ) -> dict:
        # 1. Session
        session = await session_service.get_or_create_session(
            session_id, user_id, language
        )

        # 2. Intent classification (ADK intent_classifier agent)
        classification = await classify_intent(message)
        intent     = classification.get("intent", "general_health")
        is_health  = classification.get("is_health_related", True)

        # 3. Health guardrail
        if not is_health:
            reply_text = self._OFF_TOPIC.get(language, self._OFF_TOPIC["en"])
            await session_service.add_message(session.session_id, "user", message)
            await session_service.add_message(session.session_id, "assistant", reply_text)
            return {
                "reply": reply_text,
                "session_id": session.session_id,
                "intent": "off_topic",
            }

        # 4. Persist user message
        await session_service.add_message(
            session.session_id, "user", message,
            metadata={"intent": intent, "language": language},
        )

        # 5. Route to correct sub-agent
        if intent == "symptom_query":
            result = await self._symptom_agent.run(session, message)
        elif intent == "scheduling":
            result = await self._scheduling_agent.run(session, message)
        else:
            result = await self._general_agent.run(session, message)

        # 6. Persist assistant reply
        await session_service.add_message(
            session.session_id, "assistant", result["reply"],
            metadata={"intent": intent},
        )

        result["session_id"] = session.session_id
        return result


# ── Singleton ──────────────────────────────────────────────
orchestrator = OrchestratorAgent()