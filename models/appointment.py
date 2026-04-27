"""
Appointment MongoDB Model
=========================
Defines the document structure stored in the `appointments` collection.
Used by AppointmentService for CRUD and reminder dispatch.
"""

from __future__ import annotations

import uuid
import random
import string
from datetime import datetime, timezone


def _generate_reference(length: int = 8) -> str:
    """Generate a short alphanumeric booking reference, e.g. 'HB-A3F9K2'."""
    chars = string.ascii_uppercase + string.digits
    return "HB-" + "".join(random.choices(chars, k=length))


class AppointmentDocument:
    """
    MongoDB collection: appointments

    Document shape:
    {
        "appointment_id":  str,          # UUID
        "user_id":         str,
        "appointment_type": str,         # doctor | vaccine | lab_test
        "title":           str,
        "scheduled_at":    str,          # human-readable resolved datetime string
        "doctor_name":     str,
        "location":        str,
        "status":          str,          # scheduled | confirmed | cancelled | completed
        "reference":       str,          # short booking code e.g. HB-A3F9K2
        "reminder_sent":   bool,
        "created_at":      datetime,
        "updated_at":      datetime,
    }
    """

    COLLECTION = "appointments"

    INDEXES = [
        {"key": "appointment_id", "unique": True},
        {"key": "user_id"},
        {"key": "status"},
        {"key": "scheduled_at"},
        {"key": "reminder_sent"},
    ]

    @staticmethod
    def build(
        user_id:          str,
        appointment_type: str,
        title:            str,
        scheduled_at:     str,
        doctor_name:      str = "",
        location:         str = "",
    ) -> dict:
        """Create a new appointment document ready for MongoDB insertion."""
        now = datetime.now(tz=timezone.utc)
        return {
            "appointment_id":  str(uuid.uuid4()),
            "user_id":         user_id,
            "appointment_type": appointment_type,
            "title":           title,
            "scheduled_at":    scheduled_at,
            "doctor_name":     doctor_name,
            "location":        location,
            "status":          "scheduled",
            "reference":       _generate_reference(),
            "reminder_sent":   False,
            "created_at":      now,
            "updated_at":      now,
        }

    @staticmethod
    def to_out(doc: dict) -> dict:
        """
        Convert a raw MongoDB document to a serialisable dict
        suitable for API responses and Jinja2 templates.
        """
        return {
            "id":               doc.get("appointment_id", str(doc.get("_id", ""))),
            "user_id":          doc["user_id"],
            "appointment_type": doc["appointment_type"],
            "title":            doc["title"],
            "scheduled_at":     doc["scheduled_at"],
            "doctor_name":      doc.get("doctor_name", ""),
            "location":         doc.get("location", ""),
            "status":           doc["status"],
            "reference":        doc["reference"],
            "reminder_sent":    doc.get("reminder_sent", False),
            "created_at":       doc["created_at"],
        }