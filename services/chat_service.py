"""
Chat Service
============
Persists chat messages into a dedicated `chats` collection and
provides helpers to fetch chat lists and histories for a user.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Dict, Any

from db.mongo import get_database


class ChatService:
    COLLECTION = "chats"

    async def save_message(self, session_id: str, user_id: str, role: str, content: str, metadata: dict | None = None) -> None:
        db = await get_database()
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(tz=timezone.utc).timestamp(),
            "metadata": metadata or {},
        }

        await db[self.COLLECTION].update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "session_id": session_id,
                    "user_id": user_id,
                    "updated_at": datetime.now(tz=timezone.utc),
                },
                "$push": {"messages": msg},
            },
            upsert=True,
        )

    async def get_user_chats(self, user_id: str) -> List[Dict[str, Any]]:
        db = await get_database()
        cursor = db[self.COLLECTION].find({"user_id": user_id}).sort("updated_at", -1)
        results = []
        async for doc in cursor:
            last_msg = doc.get("messages", [])[-1] if doc.get("messages") else None
            results.append({
                "session_id": doc.get("session_id"),
                "last_message": last_msg,
                "updated_at": doc.get("updated_at"),
            })
        return results

    async def get_chat_history(self, session_id: str) -> List[Dict[str, Any]]:
        db = await get_database()
        doc = await db[self.COLLECTION].find_one({"session_id": session_id})
        return doc.get("messages", []) if doc else []


chat_service = ChatService()
