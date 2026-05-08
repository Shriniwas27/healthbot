"""
User Service
============
Handles:
  - register   : create a new user with hashed password
  - login      : verify credentials, return user dict
  - get_by_id  : fetch user profile by user_id
  - get_by_email: fetch user by email
  - update     : patch user fields
"""

from __future__ import annotations

import bcrypt
from datetime import datetime, timezone
from db.mongo import get_database
from models.user import UserDocument
from utils.logger import get_logger

logger = get_logger(__name__)


class UserService:


    async def register(
        self,
        full_name:            str,
        email:                str,
        phone:                str,
        password:             str,
        age:                  int | None  = None,
        gender:               str | None  = None,
        blood_group:          str | None  = None,
        existing_conditions:  list[str]   = None,
        preferred_language:   str         = "en",
        notification_channel: str         = "sms",
    ) -> dict:
        """
        Create a new user. Raises ValueError if email already exists.
        Returns safe user dict (no password_hash).
        """
        db = await get_database()

        
        existing = await db[UserDocument.COLLECTION].find_one(
            {"email": email.lower().strip()}
        )
        if existing:
            raise ValueError("An account with this email already exists.")

        
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        doc = UserDocument.build(
            full_name=full_name,
            email=email,
            phone=phone,
            password_hash=password_hash,
            age=age,
            gender=gender,
            blood_group=blood_group,
            existing_conditions=existing_conditions or [],
            preferred_language=preferred_language,
            notification_channel=notification_channel,
        )

        await db[UserDocument.COLLECTION].insert_one(doc)
        logger.info(f"New user registered: {email} (id={doc['user_id']})")
        return UserDocument.to_safe_dict(doc)

    
    async def login(self, email: str, password: str) -> dict | None:
        """
        Verify email + password.
        Returns safe user dict on success, None on failure.
        """
        db = await get_database()
        doc = await db[UserDocument.COLLECTION].find_one(
            {"email": email.lower().strip(), "is_active": True}
        )
        if not doc:
            return None

        if not bcrypt.checkpw(
            password.encode("utf-8"),
            doc["password_hash"].encode("utf-8"),
        ):
            return None

        logger.info(f"User logged in: {email}")
        return UserDocument.to_safe_dict(doc)

    
    async def get_by_id(self, user_id: str) -> dict | None:
        db = await get_database()
        doc = await db[UserDocument.COLLECTION].find_one({"user_id": user_id})
        return UserDocument.to_safe_dict(doc) if doc else None

    async def get_by_email(self, email: str) -> dict | None:
        db = await get_database()
        doc = await db[UserDocument.COLLECTION].find_one(
            {"email": email.lower().strip()}
        )
        return UserDocument.to_safe_dict(doc) if doc else None

    
    async def update(self, user_id: str, patch: dict) -> dict | None:
        """Merge patch fields into the user document."""
        patch.pop("password_hash", None)   
        patch.pop("user_id", None)
        patch["updated_at"] = datetime.now(tz=timezone.utc)

        db = await get_database()
        result = await db[UserDocument.COLLECTION].find_one_and_update(
            {"user_id": user_id},
            {"$set": patch},
            return_document=True,
        )
        return UserDocument.to_safe_dict(result) if result else None



user_service = UserService()