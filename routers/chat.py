"""
Chat Router
===========
Handles:
  GET  /                → render chat UI (SSR)
  POST /chat            → process message, return HTML partial (HTMX)
  GET  /chat/history    → render message history partial
  POST /chat/end        → end session, force MongoDB sync
  GET  /appointments    → render appointments page (SSR)
  POST /appointments    → create appointment via form
"""

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from agents.orchestrator import orchestrator
from session import session_service
from services.appointment_service import AppointmentService
from models.schemas import Language
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="static")
appt_service = AppointmentService()


# ── Helper ─────────────────────────────────────────────────

def _get_user_id(request: Request) -> str:
    """Extract user_id from cookie (simplified — replace with real auth)."""
    return request.cookies.get("user_id", "anonymous")

def _get_session_id(request: Request) -> str | None:
    return request.cookies.get("session_id")

def _get_language(request: Request) -> str:
    return request.cookies.get("language", "en")


# ── Pages (SSR) ────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user_id = _get_user_id(request)
    session_id = _get_session_id(request)
    language = _get_language(request)

    # Restore or create session
    session = await session_service.get_or_create_session(
        session_id, user_id, language
    )

    # Fetch existing messages to hydrate the page on reload
    messages = [m.to_dict() for m in session.messages[-30:]]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "session_id": session.session_id,
            "user_id": user_id,
            "language": language,
            "messages": messages,
            "active_session_count": session_service.active_session_count,
        },
    )


@router.get("/appointments", response_class=HTMLResponse)
async def appointments_page(request: Request):
    user_id = _get_user_id(request)
    appts = await appt_service.get_user_appointments(user_id)
    return templates.TemplateResponse(
        request=request,
        name="appointments.html",
        context={"appointments": appts, "user_id": user_id},
    )


# ── Chat API (HTMX partials) ───────────────────────────────

@router.post("/chat", response_class=HTMLResponse)
async def chat(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(None),
    user_id: str = Form("anonymous"),
    language: str = Form("en"),
):
    """
    Core chat endpoint.
    Returns an HTML partial (HTMX swap) with:
      - The user's message bubble
      - The assistant's response bubble
      - Any follow-up question buttons
      - Any appointment confirmation card
    """
    if not message.strip():
        return HTMLResponse("<div></div>")

    try:
        result = await orchestrator.process(
            user_id=user_id,
            message=message,
            session_id=session_id or None,
            language=language,
        )
    except Exception as e:
        logger.error(f"Orchestrator error: {e}")
        result = {
            "reply": "Sorry, I encountered an error. Please try again.",
            "session_id": session_id or "",
            "intent": "error",
            "follow_up_questions": [],
            "probable_conditions": [],
            "appointment_created": None,
        }

    return templates.TemplateResponse(
        request=request,
        name="partials/chat_turn.html",
        context={
            "user_message": message,
            "reply": result["reply"],
            "session_id": result["session_id"],
            "intent": result.get("intent"),
            "follow_up_questions": result.get("follow_up_questions", []),
            "probable_conditions": result.get("probable_conditions", []),
            "appointment_created": result.get("appointment_created"),
        },
    )


@router.get("/chat/history", response_class=HTMLResponse)
async def chat_history(request: Request):
    """Return message history partial for the current session."""
    session_id = _get_session_id(request)
    if not session_id:
        return HTMLResponse("")

    session = await session_service.get_session(session_id)
    if not session:
        return HTMLResponse("")

    messages = [m.to_dict() for m in session.messages]
    return templates.TemplateResponse(
        request=request,
        name="partials/history.html",
        context={"messages": messages},
    )


@router.post("/chat/end", response_class=HTMLResponse)
async def end_chat(
    request: Request,
    session_id: str = Form(...),
):
    """Force sync session to MongoDB and clear it from memory."""
    await session_service.end_session(session_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/session_ended.html",
    )


@router.post("/appointments/cancel", response_class=HTMLResponse)
async def cancel_appointment(
    request: Request,
    appointment_id: str = Form(...),
):
    user_id = _get_user_id(request)
    success = await appt_service.cancel_appointment(appointment_id, user_id)
    appts = await appt_service.get_user_appointments(user_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/appointments_list.html",
        context={"appointments": appts, "cancelled": success},
    )


# ── Twilio Webhook (reply CONFIRM/CANCEL via SMS) ──────────

@router.post("/webhooks/twilio/reply")
async def twilio_reply(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
):
    """Handle CONFIRM/CANCEL SMS replies from users."""
    from db.mongo import get_database
    db = await get_database()

    body = Body.strip().upper()
    phone = From.strip()

    if body in ("CONFIRM", "CANCEL"):
        status = "confirmed" if body == "CONFIRM" else "cancelled"
        await db.appointments.update_many(
            {
                "status": "scheduled",
                "reminder_sent": True,
            },
            {"$set": {"status": status}},
        )
        reply_msg = (
            "Your appointment has been confirmed. See you soon!"
            if status == "confirmed"
            else "Your appointment has been cancelled. Stay healthy!"
        )
    else:
        reply_msg = "Reply CONFIRM to confirm or CANCEL to cancel your appointment."

    # Return TwiML response
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response><Message>{reply_msg}</Message></Response>"""
    return HTMLResponse(content=twiml, media_type="application/xml")