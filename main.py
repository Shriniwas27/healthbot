"""
Health Chatbot — Main FastAPI Application (ADK edition)
=======================================================
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
    symptom_agent_llm,
    diagnosis_agent_llm,
    general_health_agent_llm,
    summary_agent_llm,
    scheduling_extractor_agent,
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
    app.state.intent_classifier_agent    = intent_classifier_agent
    app.state.symptom_agent_llm          = symptom_agent_llm
    app.state.diagnosis_agent_llm        = diagnosis_agent_llm
    app.state.general_health_agent_llm   = general_health_agent_llm
    app.state.summary_agent_llm          = summary_agent_llm
    app.state.scheduling_extractor_agent = scheduling_extractor_agent
    app.state.adk_session_service        = _adk_session_service

    logger.info(f"ADK SESSION SERVICE ID: {id(app.state.adk_session_service)}")

    scheduler.add_job(_run_reminders,    "interval", minutes=15, id="reminders")
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

# ── Routers ────────────────────────────────────────────────
app.include_router(auth_router)     # /register, /login, /logout
app.include_router(chat_router)     # /, /chat, /appointments, /webhooks


# ── Health Check ───────────────────────────────────────────

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


# ── Dev Entry Point ────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )