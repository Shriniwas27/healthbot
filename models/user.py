"""
User MongoDB Model
==================
Document structure for the `users` collection.
Stores registration details, contact info for reminders,
and a hashed password for authentication.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


class UserDocument:
    """
    MongoDB collection: users

    Document shape:
    {
        "user_id":       str,        # UUID — primary key used across all collections
        "full_name":     str,
        "email":         str,        # unique login identifier
        "phone":         str,        # E.164 format e.g. +919876543210
        "password_hash": str,        # bcrypt hash — NEVER store plaintext
        "age":           int | None,
        "gender":        str | None, # male | female | other | prefer_not_to_say
        "blood_group":   str | None, # A+ | A- | B+ | B- | O+ | O- | AB+ | AB-
        "existing_conditions": list[str],  # e.g. ["diabetes", "hypertension"]
        "preferred_language": str,   # ISO 639-1 e.g. "en", "hi", "mr"
        "notification_channel": str, # sms | whatsapp | both
        "is_active":     bool,
        "created_at":    datetime,
        "updated_at":    datetime,
    }
    """

    COLLECTION = "users"

    INDEXES = [
        {"key": "user_id",  "unique": True},
        {"key": "email",    "unique": True},
        {"key": "phone"},
    ]

    @staticmethod
    def build(
        full_name:            str,
        email:                str,
        phone:                str,
        password_hash:        str,
        age:                  int | None  = None,
        gender:               str | None  = None,
        blood_group:          str | None  = None,
        existing_conditions:  list[str]   = None,
        preferred_language:   str         = "en",
        notification_channel: str         = "sms",
    ) -> dict:
        now = datetime.now(tz=timezone.utc)
        return {
            "user_id":               str(uuid.uuid4()),
            "full_name":             full_name,
            "email":                 email.lower().strip(),
            "phone":                 phone.strip(),
            "password_hash":         password_hash,
            "age":                   age,
            "gender":                gender,
            "blood_group":           blood_group,
            "existing_conditions":   existing_conditions or [],
            "preferred_language":    preferred_language,
            "notification_channel":  notification_channel,
            "is_active":             True,
            "created_at":            now,
            "updated_at":            now,
        }

    @staticmethod
    def to_safe_dict(doc: dict) -> dict:
        """Return user dict safe for API responses — strips password_hash."""
        return {
            "user_id":               doc["user_id"],
            "full_name":             doc["full_name"],
            "email":                 doc["email"],
            "phone":                 doc["phone"],
            "age":                   doc.get("age"),
            "gender":                doc.get("gender"),
            "blood_group":           doc.get("blood_group"),
            "existing_conditions":   doc.get("existing_conditions", []),
            "preferred_language":    doc.get("preferred_language", "en"),
            "notification_channel":  doc.get("notification_channel", "sms"),
            "is_active":             doc.get("is_active", True),
            "created_at":            doc["created_at"],
        }