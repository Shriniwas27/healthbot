"""
Hybrid Session Service
======================
Strategy:
  - InMemorySession  → holds full active conversation (fast, no DB calls per turn)
  - MongoDB          → stores a rolling SUMMARY + metadata (persists across restarts)

Sync happens:
  1. Every N messages (configurable via SUMMARY_SYNC_INTERVAL)
  2. On explicit session end / user logout
  3. On server startup → warm InMemory from MongoDB summary if session exists

Gemini context for a returning user:
  - Active messages  → from InMemory
  - Previous-session summary → from MongoDB
  - Both injected as system context → model always has full picture

NOTE: The only change vs. the original is that _generate_summary() now imports
      from agents.gemini_client instead of agents.gemini_client (same path kept)
      and relies on the ADK-backed generate_summary() helper.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from db.mongo import get_database
from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────

@dataclass
class Message:
    role: str                   
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(d: dict) -> "Message":
        return Message(
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", time.time()),
            metadata=d.get("metadata", {}),
        )


@dataclass
class InMemorySession:
    session_id: str
    user_id: str
    language: str = "en"
    messages: list[Message] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    messages_since_sync: int = 0
    is_dirty: bool = False

    def add_message(self, role: str, content: str, metadata: dict = None) -> Message:
        msg = Message(role=role, content=content, metadata=metadata or {})
        self.messages.append(msg)
        self.last_active = time.time()
        self.messages_since_sync += 1
        self.is_dirty = True
        return msg

    def get_recent_messages(self, n: int = 20) -> list[Message]:
        return self.messages[-n:]

    def is_expired(self, ttl: int) -> bool:
        return (time.time() - self.last_active) > ttl

    def should_sync(self, interval: int) -> bool:
        return self.messages_since_sync >= interval


# ─────────────────────────────────────────────
# MongoDB Document Schema
# ─────────────────────────────────────────────

class SessionDocument:
    COLLECTION = "sessions"

    @staticmethod
    def build(session: InMemorySession, summary: str) -> dict:
        return {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "language": session.language,
            "summary": summary,
            "state": session.state,
            "message_count": len(session.messages),
            "last_active": datetime.fromtimestamp(session.last_active, tz=timezone.utc),
            "created_at": datetime.fromtimestamp(session.created_at, tz=timezone.utc),
            "full_history": [m.to_dict() for m in session.messages[-50:]],
            "updated_at": datetime.now(tz=timezone.utc),
        }


# ─────────────────────────────────────────────
# Hybrid Session Service
# ─────────────────────────────────────────────

class HybridSessionService:
    """
    InMemory dict  →  primary store for active conversations.
    MongoDB        →  durable store: summary + state + last 50 messages.

    The Gemini context builder pulls from BOTH:
      [system: previous session summary] + [recent InMemory messages]
    """

    def __init__(self):
        self._sessions: dict[str, InMemorySession] = {}
        self._lock = asyncio.Lock()

    # ── Session Lifecycle ──────────────────────────────────

    async def create_session(
        self,
        user_id: str,
        language: str = "en",
        initial_state: dict = None,
    ) -> InMemorySession:
        session_id = str(uuid.uuid4())
        session = InMemorySession(
            session_id=session_id,
            user_id=user_id,
            language=language,
            state=initial_state or {
                "symptom_context": {},
                "triage_stage": 0,
                "appointments": [],
                "last_intent": None,
            },
        )
        async with self._lock:
            self._sessions[session_id] = session
        logger.info(f"Session created: {session_id} for user: {user_id}")
        return session

    async def get_session(self, session_id: str) -> InMemorySession | None:
        async with self._lock:
            session = self._sessions.get(session_id)

        if session:
            if session.is_expired(settings.session_ttl_seconds):
                await self.end_session(session_id)
                return None
            session.last_active = time.time()
            return session

        logger.info(f"Session {session_id} not in memory — attempting MongoDB restore")
        return await self._restore_from_mongo(session_id)

    async def get_or_create_session(
        self,
        session_id: str | None,
        user_id: str,
        language: str = "en",
    ) -> InMemorySession:
        if session_id:
            session = await self.get_session(session_id)
            if session:
                return session

        existing = await self._load_latest_user_session(user_id)
        if existing:
            return existing

        return await self.create_session(user_id, language)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict = None,
    ) -> Message | None:
        session = await self.get_session(session_id)
        if not session:
            return None

        msg = session.add_message(role, content, metadata)

        if session.should_sync(settings.summary_sync_interval):
            asyncio.create_task(self._sync_to_mongo(session))

        return msg

    async def end_session(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session and session.is_dirty:
            await self._sync_to_mongo(session)
            logger.info(f"Session {session_id} ended and synced.")

    async def update_state(self, session_id: str, patch: dict) -> None:
        session = await self.get_session(session_id)
        if session:
            session.state.update(patch)
            session.is_dirty = True

    # ── Context Builder for ADK-backed Gemini calls ────────

    async def build_gemini_context(self, session_id: str) -> list[dict]:
        """
        Returns message dicts compatible with the generate_response() helper
        in gemini_client.py (which now uses ADK LlmAgent under the hood).

        Format:
          [{"role": "user"|"model", "parts": [{"text": "..."}]}, ...]
        """
        session = await self.get_session(session_id)
        if not session:
            return []

        context_messages: list[dict] = []

        # Inject MongoDB summary as leading context
        mongo_doc = await self._fetch_mongo_doc(session_id)
        if mongo_doc and mongo_doc.get("summary"):
            summary_text = (
                f"[Previous conversation summary]\n{mongo_doc['summary']}\n"
                f"[End of summary — continue the conversation naturally]"
            )
            context_messages.append({"role": "user",  "parts": [{"text": summary_text}]})
            context_messages.append({"role": "model", "parts": [{"text": "Understood. I have the context from our previous conversation."}]})

        # Append recent InMemory messages (last 20)
        for msg in session.get_recent_messages(20):
            gemini_role = "model" if msg.role == "assistant" else "user"
            context_messages.append({"role": gemini_role, "parts": [{"text": msg.content}]})

        return context_messages

    # ── MongoDB Sync ───────────────────────────────────────

    async def _sync_to_mongo(self, session: InMemorySession) -> None:
        try:
            summary = await self._generate_summary(session)
            db = await get_database()
            doc = SessionDocument.build(session, summary)

            await db[SessionDocument.COLLECTION].update_one(
                {"session_id": session.session_id},
                {"$set": doc},
                upsert=True,
            )

            session.messages_since_sync = 0
            session.is_dirty = False
            logger.info(
                f"Session {session.session_id} synced to MongoDB "
                f"({len(session.messages)} messages)"
            )
        except Exception as e:
            logger.error(f"MongoDB sync failed for {session.session_id}: {e}")

    async def _restore_from_mongo(self, session_id: str) -> InMemorySession | None:
        try:
            doc = await self._fetch_mongo_doc(session_id)
            if not doc:
                return None

            session = InMemorySession(
                session_id=doc["session_id"],
                user_id=doc["user_id"],
                language=doc.get("language", "en"),
                state=doc.get("state", {}),
                created_at=doc["created_at"].timestamp(),
                last_active=doc["last_active"].timestamp(),
            )
            for m in doc.get("full_history", []):
                session.messages.append(Message.from_dict(m))

            session.messages_since_sync = 0
            session.is_dirty = False

            async with self._lock:
                self._sessions[session_id] = session

            logger.info(f"Session {session_id} restored from MongoDB")
            return session
        except Exception as e:
            logger.error(f"Session restore failed for {session_id}: {e}")
            return None

    async def _load_latest_user_session(self, user_id: str) -> InMemorySession | None:
        try:
            db = await get_database()
            doc = await db[SessionDocument.COLLECTION].find_one(
                {"user_id": user_id},
                sort=[("last_active", -1)],
            )
            if not doc:
                return None
            return await self._restore_from_mongo(doc["session_id"])
        except Exception as e:
            logger.error(f"Failed to load user session: {e}")
            return None

    async def _fetch_mongo_doc(self, session_id: str) -> dict | None:
        try:
            db = await get_database()
            return await db[SessionDocument.COLLECTION].find_one({"session_id": session_id})
        except Exception:
            return None

    async def _generate_summary(self, session: InMemorySession) -> str:
        """
        Uses the ADK-backed generate_summary() from gemini_client.py
        (which internally runs the summariser LlmAgent).
        """
        # Deferred import avoids circular dependency
        from agents.gemini_client import generate_summary

        transcript = "\n".join(
            f"{m.role.upper()}: {m.content}"
            for m in session.messages[-30:]
        )
        try:
            return await generate_summary(transcript, session.language)
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return " | ".join(m.content for m in session.messages[-5:])

    # ── Cleanup ────────────────────────────────────────────

    async def cleanup_expired(self) -> int:
        expired: list[str] = []
        async with self._lock:
            for sid, session in self._sessions.items():
                if session.is_expired(settings.session_ttl_seconds):
                    expired.append(sid)

        for sid in expired:
            await self.end_session(sid)

        if expired:
            logger.info(f"Evicted {len(expired)} expired sessions")
        return len(expired)

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)


# ── Singleton ──────────────────────────────────────────────
session_service = HybridSessionService()