"""
Health Chatbot — Main FastAPI Application (ADK edition)
=======================================================
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import get_settings
from db.mongo import connect_db, close_db
from routers.chat import router as chat_router
from routers.auth import router as auth_router          # ← auth routes
from session import session_service
from services.appointment_service import ReminderService
from utils.logger import get_logger

# ── Import ADK agents ──────────────────────────────────────
from agents.gemini_client import (
    intent_classifier_agent,
    health_agent_llm,
    summary_agent_llm,
    _adk_session_service,
)

logger          = get_logger(__name__)
settings        = get_settings()
scheduler       = AsyncIOScheduler()
reminder_service = ReminderService()


# ── Scheduler Jobs ─────────────────────────────────────────

async def _run_reminders():
    count = await reminder_service.dispatch_due_reminders()
    if count:
        logger.info(f"Reminders sent: {count}")


async def _cleanup_sessions():
    count = await session_service.cleanup_expired()
    if count:
        logger.info(f"Expired sessions cleaned: {count}")


# ── Lifespan ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Health Chatbot (ADK edition)...")
    await connect_db()

    # Store ADK agents in app.state
    app.state.intent_classifier_agent = intent_classifier_agent
    app.state.health_agent_llm = health_agent_llm
    app.state.summary_agent_llm = summary_agent_llm
    app.state.adk_session_service = _adk_session_service

    logger.info(f"ADK SESSION SERVICE ID: {id(app.state.adk_session_service)}")

    scheduler.add_job(_run_reminders,    "interval", minutes=1, id="reminders")
    scheduler.add_job(_cleanup_sessions, "interval", minutes=10, id="session_cleanup")
    scheduler.start()
    logger.info("APScheduler started")

    yield

    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)

    for sid in list(session_service._sessions.keys()):
        await session_service.end_session(sid)

    await close_db()
    logger.info("Shutdown complete")


# ── App ────────────────────────────────────────────────────

app = FastAPI(
    title="Health Chatbot",
    description="AI-powered multilingual health assistant — powered by Google ADK",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


app.include_router(auth_router)    
app.include_router(chat_router)     

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.json()
    except Exception:
        raw = await request.body()
        body = raw.decode('utf-8', errors='replace')

    # Sanitize exc.errors() so any bytes are converted to strings and JSON serializable
    errors = []
    for err in exc.errors():
        err_copy = dict(err)
        if 'input' in err_copy and isinstance(err_copy['input'], (bytes, bytearray)):
            try:
                err_copy['input'] = err_copy['input'].decode('utf-8', errors='replace')
            except Exception:
                err_copy['input'] = repr(err_copy['input'])
        errors.append(err_copy)

    logger.error("Request validation error for %s %s: %s — body=%s",
                 request.method, request.url.path, errors, body)

    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errors, "body": body}))



@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "active_sessions": session_service.active_session_count,
        "adk_session_service_id": id(app.state.adk_session_service),
        "agents": [
            "intent_classifier",
            "symptom_collector",
            "diagnosis_analyser",
            "general_health",
            "summariser",
            "scheduling_extractor",
        ],
    }



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )