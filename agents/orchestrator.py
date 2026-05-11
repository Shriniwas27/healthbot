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
    extract_scheduling_details,
)
from session import session_service, InMemorySession
from services.chat_service import chat_service
from services.appointment_service import AppointmentService
from utils.logger import get_logger

logger = get_logger(__name__)


class SymptomAgent:
    """
    Collects symptoms via follow-up questions.
    Triggers DiagnosisAgent once 3 triage stages are complete.
    """

    async def run(self, session: InMemorySession, user_message: str, language: str = "en") -> dict:
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
            follow_ups = await generate_follow_up_questions(
                symptoms, stage, language
            )
            reply = (
                f"{follow_ups[0]}\n\n{follow_ups[1]}"
                if len(follow_ups) >= 2
                else (follow_ups[0] if follow_ups else "Can you describe your symptoms more?")
            )
            state["triage_stage"] = stage + 1
        else:
            user_profile = {
                "age": state.get("user_age"),
                "conditions": state.get("existing_conditions", []),
            }
            raw_conditions = await analyse_probable_conditions(
                symptoms, answers, user_profile, language
            )

            # Strip disclaimer dict — keep only real condition dicts
            probable_conditions = [
                c for c in raw_conditions
                if "name" in c and "confidence" in c
            ]

            conditions_text = "\n".join(
                f"- {c['name']} ({int(c['confidence'] * 100)}%): {c['recommendation']}"
                for c in probable_conditions
            )
            reply = (
                f"Based on what you've described, here are probable conditions:\n\n"
                f"{conditions_text}\n\n"
                f"This is not a medical diagnosis. Please consult a qualified doctor."
            )
            state["triage_stage"] = 0
            ctx["last_conditions"] = probable_conditions  # clean dicts only

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
        details = await extract_scheduling_details(user_message)
        missing = [k for k, v in details.items() if v is None and k != "doctor_name"]
        if missing and not details.get("title"):
            return {
                "reply": (
                    "I'd be happy to schedule that! "
                    "Could you please provide the preferred date and time?"
                ),
                "intent": "scheduling",
                "appointment_created": None,
            }

        if not details.get("date_hint"):
            return {
                "reply": (
                    "I'd be happy to schedule that! "
                    "Please tell me the exact date and time for the appointment."
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
            f"Appointment scheduled!\n"
            f"{appt['title']} on {appt['scheduled_at']}\n"
            f"{appt.get('location', 'TBD')}\n"
            f"Reference: {appt['reference']}\n\n"
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

    async def run(self, session: InMemorySession, user_message: str, language: str = "en") -> dict:
        context = await session_service.build_gemini_context(session.session_id)
        reply = await generate_response(context, user_message, language)
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
      2. If mid-triage (triage_stage > 0) → skip classification, route directly to SymptomAgent
      3. Classify intent  (ADK intent_classifier agent — fast Flash call)
      4. Health guardrail check
      5. Persist user message
      6. Route → SymptomAgent | SchedulingAgent | GeneralHealthAgent
      7. Persist assistant reply
      8. Return result dict to chat router
    """

    _OFF_TOPIC: dict[str, str] = {
        "en": "I can only assist with health-related queries. How can I help with your health today?",
        "hi": "मैं केवल स्वास्थ्य संबंधी प्रश्नों में मदद कर सकता हूँ। आज मैं आपके स्वास्थ्य के बारे में कैसे मदद कर सकता हूँ?",
        "mr": "मी फक्त आरोग्याशी संबंधित प्रश्नांना उत्तर देऊ शकतो. आज मी तुमच्या आरोग्याबाबत कशी मदत करू शकतो?",
        "es": "Solo puedo ayudar con consultas relacionadas con la salud. ¿Cómo puedo ayudarte con tu salud hoy?",
        "fr": "Je ne peux aider qu'avec des questions liées à la santé. Comment puis-je vous aider avec votre santé aujourd'hui?",
        "ar": "يمكنني فقط المساعدة في الاستفسارات المتعلقة بالصحة. كيف يمكنني مساعدتك في صحتك اليوم؟",
        "ta": "நான் உடல்நலம் தொடர்பான கேள்விகளுக்கு மட்டுமே உதவ முடியும். இன்று உங்கள் உடல்நலம் பற்றி எவ்வாறு உதவலாம்?",
        "te": "నేను ఆరోగ్య సంబంధిత ప్రశ్నలకు మాత్రమే సహాయం చేయగలను. ఈరోజు మీ ఆరోగ్యం గురించి నేను ఎలా సహాయపడగలను?",
        "kn": "ನಾನು ಆರೋಗ್ಯ ಸಂಬಂಧಿತ ಪ್ರಶ್ನೆಗಳಿಗೆ ಮಾತ್ರ ಸಹಾಯ ಮಾಡಬಲ್ಲೆ. ಇಂದು ನಿಮ್ಮ ಆರೋಗ್ಯದ ಬಗ್ಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
        "ml": "എനിക്ക് ആരോഗ്യ സംബന്ധമായ ചോദ്യങ്ങൾക്ക് മാത്രമേ സഹായിക്കാൻ കഴിയും. ഇന്ന് നിങ്ങളുടെ ആരോഗ്യത്തെക്കുറിച്ച് എങ്ങനെ സഹായിക്കാം?",
        "gu": "હું ફક્ત આરોગ્ય સંબંધિત પ્રશ્નોમાં મદદ કરી શકું છું. આજે હું તમારા સ્વાસ્થ્ય વિશે કેવી રીતે મદદ કરી શકું?",
        "pa": "ਮੈਂ ਸਿਰਫ਼ ਸਿਹਤ ਸੰਬੰਧੀ ਸਵਾਲਾਂ ਵਿੱਚ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ। ਅੱਜ ਮੈਂ ਤੁਹਾਡੀ ਸਿਹਤ ਬਾਰੇ ਕਿਵੇਂ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ?",
        "bn": "আমি শুধুমাত্র স্বাস্থ্য সম্পর্কিত প্রশ্নে সাহায্য করতে পারি। আজ আপনার স্বাস্থ্য বিষয়ে কীভাবে সাহায্য করতে পারি?",
        "de": "Ich kann nur bei gesundheitsbezogenen Anfragen helfen. Wie kann ich Ihnen heute bei Ihrer Gesundheit helfen?",
        "pt": "Só posso ajudar com questões relacionadas à saúde. Como posso ajudá-lo com sua saúde hoje?",
        "ru": "Я могу помочь только с вопросами, связанными со здоровьем. Как я могу помочь вам со здоровьем сегодня?",
        "zh": "我只能协助解答与健康相关的问题。今天我能如何帮助您的健康？",
        "ja": "健康に関するご質問のみお答えできます。本日、健康についてどのようにお手伝いできますか？",
        "ko": "저는 건강 관련 질문만 도와드릴 수 있습니다. 오늘 건강에 대해 어떻게 도와드릴까요?",
        "ur": "میں صرف صحت سے متعلق سوالات میں مدد کر سکتا ہوں۔ آج میں آپ کی صحت کے بارے میں کیسے مدد کر سکتا ہوں؟",
    }

    def __init__(self):
        self._symptom_agent    = SymptomAgent()
        self._scheduling_agent = SchedulingAgent()
        self._general_agent    = GeneralHealthAgent()

    async def process(
        self,
        user_id: str,
        message: str,
        session_id: str | None = None,
    ) -> dict:
        # 1. Session
        session = await session_service.get_or_create_session(
            session_id, user_id
        )

        state        = session.state
        triage_stage = state.get("triage_stage", 0)

        # 2. Mid-triage shortcut — skip classification entirely
        #    User is answering the bot's own follow-up questions, so short
        #    context-free answers like "8" or "my forehead" won't be
        #    misclassified as off-topic.
        if triage_stage > 0:
            session_language = state.get("language", "en")

            await session_service.add_message(
                session.session_id, "user", message,
                metadata={"intent": "symptom_query"},
            )
            await chat_service.save_message(
                session.session_id, session.user_id, "user", message,
                metadata={"intent": "symptom_query"},
            )

            result = await self._symptom_agent.run(session, message, session_language)

            await session_service.add_message(
                session.session_id, "assistant", result["reply"],
                metadata={"intent": "symptom_query"},
            )
            await chat_service.save_message(
                session.session_id, session.user_id, "assistant", result["reply"],
                metadata={"intent": "symptom_query"},
            )

            result["session_id"] = session.session_id
            return result

        # 3. Normal flow — classify intent
        classification = await classify_intent(message)
        intent    = classification.get("intent", "general_health")
        is_health = classification.get("is_health_related", True)
        language  = classification.get("language", "en")

        # Lock language in session on first detection — prevents per-turn drift
        if not state.get("language") and language:
            state["language"] = language
            await session_service.update_state(session.session_id, state)

        session_language = state.get("language") or language

        # 4. Health guardrail
        if not is_health:
            reply_text = self._OFF_TOPIC.get(session_language, self._OFF_TOPIC["en"])
            await session_service.add_message(session.session_id, "user", message)
            await chat_service.save_message(session.session_id, session.user_id, "user", message, metadata={"intent": "off_topic"})
            await session_service.add_message(session.session_id, "assistant", reply_text)
            await chat_service.save_message(session.session_id, session.user_id, "assistant", reply_text, metadata={"intent": "off_topic"})
            return {
                "reply": reply_text,
                "session_id": session.session_id,
                "intent": "off_topic",
            }

        # 5. Persist user message
        await session_service.add_message(
            session.session_id, "user", message,
            metadata={"intent": intent},
        )
        await chat_service.save_message(session.session_id, session.user_id, "user", message, metadata={"intent": intent})

        # 6. Route to correct sub-agent
        if intent == "symptom_query":
            result = await self._symptom_agent.run(session, message, session_language)
        elif intent == "scheduling":
            result = await self._scheduling_agent.run(session, message)
        else:
            result = await self._general_agent.run(session, message, session_language)

        # 7. Persist assistant reply
        await session_service.add_message(
            session.session_id, "assistant", result["reply"],
            metadata={"intent": intent},
        )
        await chat_service.save_message(session.session_id, session.user_id, "assistant", result["reply"], metadata={"intent": intent})

        result["session_id"] = session.session_id
        return result


orchestrator = OrchestratorAgent()