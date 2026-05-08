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

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from models.appointment import AppointmentDocument
from models.user import UserDocument
from datetime import datetime, timezone
from agents.orchestrator import orchestrator
from session import session_service
from services.appointment_service import AppointmentService
from services.chat_service import chat_service
from services.user_service import user_service
from models.schemas import Language
from utils.logger import get_logger
from fastapi.responses import RedirectResponse
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
async def index(request: Request, fresh: bool = False):
    user_id = _get_user_id(request)
    session_id = _get_session_id(request)
    if user_id == "anonymous":
   
        return RedirectResponse(url="/login", status_code=302)
    
    if fresh:
        session = await session_service.create_session(user_id)
    else:
        session = await session_service.get_or_create_session(
            session_id, user_id
        )

    messages = await chat_service.get_chat_history(session.session_id)

    user = await user_service.get_by_id(user_id)
    display_name = user.get("full_name") if user else ""

    resp = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "session_id": session.session_id,
            "user_id": user_id,
            "messages": messages,
            "active_session_count": session_service.active_session_count,
            "display_name": display_name,
        },
    )
    resp.set_cookie("session_id", session.session_id, httponly=True, samesite="lax")
    return resp


@router.get("/chats/load", response_class=HTMLResponse)
async def load_chat(request: Request, session_id: str):
    """Set `session_id` cookie and return the chat history to swap into the chat container."""
    messages = await chat_service.get_chat_history(session_id)

    resp = templates.TemplateResponse(
        request=request,
        name="partials/history_swap.html",
        context={"messages": messages, "session_id": session_id},
    )
    resp.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    return resp


@router.get("/appointments", response_class=HTMLResponse)
async def appointments_page(request: Request):
    user_id = _get_user_id(request)
    appts = await appt_service.get_user_appointments(user_id)

    if request.headers.get("HX-Request") or request.headers.get("hx-request"):
        return templates.TemplateResponse(
            request=request,
            name="partials/appointments_list.html",
            context={"appointments": appts, "user_id": user_id},
        )

    return templates.TemplateResponse(
        request=request,
        name="appointments.html",
        context={"appointments": appts, "user_id": user_id},
    )



@router.post("/chat", response_class=HTMLResponse)
async def chat(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(None),
    user_id: str = Form("anonymous"),
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
    messages = await chat_service.get_chat_history(session_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/history.html",
        context={"messages": messages},
    )


@router.get("/chats", response_class=HTMLResponse)
async def chats_list(request: Request):
    """Return a partial listing of previous chats for the current user."""
    user_id = _get_user_id(request)
    if user_id == "anonymous":
        return HTMLResponse("")

    chats = await chat_service.get_user_chats(user_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/chats_list.html",
        context={"chats": chats},
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



@router.post("/webhooks/twilio/reply")
async def twilio_reply(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
):
    from db.mongo import get_database
    db = await get_database()

    body = Body.strip().upper()
    phone = From.strip().removeprefix("whatsapp:")

    # Look up user by phone (stored in E.164 format, no prefix)
    user = await db[UserDocument.COLLECTION].find_one({"phone": phone})
    if not user:
        logger.warning(f"Twilio webhook: no user found for phone {phone}")
        twiml = "<?xml version='1.0'?><Response><Message>We couldn't find your account.</Message></Response>"
        return HTMLResponse(content=twiml, media_type="application/xml")

    if body not in ("CONFIRM", "CANCEL"):
        twiml = "<?xml version='1.0'?><Response><Message>Reply CONFIRM to confirm or CANCEL to cancel your appointment.</Message></Response>"
        return HTMLResponse(content=twiml, media_type="application/xml")

    status = "confirmed" if body == "CONFIRM" else "cancelled"

    # Find the single most recent reminded appointment for this user
    # (reminder_sent=True means reminder was just dispatched — most likely the one they're replying to)
    appt = await db[AppointmentDocument.COLLECTION].find_one(
        {
            "user_id": user["user_id"],
            "status": "scheduled",
            "reminder_sent": True,
        },
        sort=[("scheduled_at", 1)],  # earliest upcoming = the one they're replying about
    )

    if not appt:
        twiml = "<?xml version='1.0'?><Response><Message>No pending appointment found to update.</Message></Response>"
        return HTMLResponse(content=twiml, media_type="application/xml")

    await db[AppointmentDocument.COLLECTION].update_one(
        {"appointment_id": appt["appointment_id"]},
        {"$set": {
            "status": status,
            "updated_at": datetime.now(tz=timezone.utc),
        }},
    )

    reply_msg = (
        "Your appointment has been confirmed. See you soon!"
        if status == "confirmed"
        else "Your appointment has been cancelled. Stay healthy!"
    )
    twiml = f"<?xml version='1.0' encoding='UTF-8'?><Response><Message>{reply_msg}</Message></Response>"
    return HTMLResponse(content=twiml, media_type="application/xml")