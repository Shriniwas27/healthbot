"""
Session MongoDB Model
=====================
Defines the document structure stored in the `sessions` collection.
Used by HybridSessionService for persistence and warm restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SessionDocument:
    """
    MongoDB collection: sessions

    Document shape:
    {
        "session_id":    str,          # unique — also the lookup key
        "user_id":       str,
        "language":      str,          # ISO 639-1 code
        "summary":       str,          # Gemini-generated rolling summary
        "state":         dict,         # symptom_context, triage_stage, etc.
        "message_count": int,
        "last_active":   datetime,
        "created_at":    datetime,
        "updated_at":    datetime,
        "full_history":  list[dict],   # last 50 messages verbatim
    }
    """

    COLLECTION = "sessions"

    # Indexes to create on first run (see db/mongo.py _ensure_indexes)
    INDEXES = [
        {"key": "session_id", "unique": True},
        {"key": "user_id"},
        {"key": "last_active"},
    ]

    @staticmethod
    def build(session: Any, summary: str) -> dict:
        """
        Build a MongoDB-ready dict from an InMemorySession + summary string.
        `session` is typed as Any to avoid circular imports with hybrid_session.py.
        """
        return {
            "session_id":    session.session_id,
            "user_id":       session.user_id,
            "language":      session.language,
            "summary":       summary,
            "state":         session.state,
            "message_count": len(session.messages),
            "last_active":   datetime.fromtimestamp(
                                 session.last_active, tz=timezone.utc
                             ),
            "created_at":    datetime.fromtimestamp(
                                 session.created_at, tz=timezone.utc
                             ),
            "full_history":  [
                                 m.to_dict() for m in session.messages[-50:]
                             ],
            "updated_at":    datetime.now(tz=timezone.utc),
        }

    @staticmethod
    def from_doc(doc: dict) -> dict:
        """
        Normalise a raw MongoDB document into a clean dict
        safe to pass around the app.
        """
        return {
            "session_id":    doc["session_id"],
            "user_id":       doc["user_id"],
            "language":      doc.get("language", "en"),
            "summary":       doc.get("summary", ""),
            "state":         doc.get("state", {}),
            "message_count": doc.get("message_count", 0),
            "last_active":   doc["last_active"],
            "created_at":    doc["created_at"],
            "full_history":  doc.get("full_history", []),
        }