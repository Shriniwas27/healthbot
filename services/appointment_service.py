"""
Appointment Service
===================
Handles appointment CRUD and reminder dispatch.
Fetches user contact info from MongoDB to send
personalised Twilio SMS/WhatsApp notifications.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta

from db.mongo import get_database
from models.appointment import AppointmentDocument
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Date hint resolver
# ─────────────────────────────────────────────

def _resolve_date(date_hint: str) -> str:
    if not date_hint:
        return "TBD"

    hint = date_hint.lower().strip()
    now  = datetime.now(tz=timezone.utc)

    if "tomorrow"   in hint: base = now + timedelta(days=1)
    elif "today"    in hint: base = now
    elif "next week" in hint: base = now + timedelta(weeks=1)
    elif "monday"    in hint: base = now + timedelta(days=(0 - now.weekday()) % 7 or 7)
    elif "tuesday"   in hint: base = now + timedelta(days=(1 - now.weekday()) % 7 or 7)
    elif "wednesday" in hint: base = now + timedelta(days=(2 - now.weekday()) % 7 or 7)
    elif "thursday"  in hint: base = now + timedelta(days=(3 - now.weekday()) % 7 or 7)
    elif "friday"    in hint: base = now + timedelta(days=(4 - now.weekday()) % 7 or 7)
    else: base = now + timedelta(days=1)

    hour = 9
    time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', hint)
    if time_match:
        h        = int(time_match.group(1))
        meridiem = time_match.group(3)
        if meridiem == "pm" and h != 12: h += 12
        elif meridiem == "am" and h == 12: h = 0
        hour = h

    resolved = base.replace(hour=hour, minute=0, second=0, microsecond=0)
    return resolved.strftime("%d %b %Y, %I:%M %p")


async def _get_user(user_id: str) -> dict | None:
    """Fetch user document for notification purposes."""
    try:
        db  = await get_database()
        doc = await db["users"].find_one({"user_id": user_id})
        if not doc:
            # Fallback for anonymous/cookie-based users
            return {
                "phone": user_id if user_id.startswith("+") else "",
                "notification_channel": "sms",
                "full_name": "User",
                "preferred_language": "en",
            }
        return doc
    except Exception as e:
        logger.error(f"Failed to fetch user {user_id}: {e}")
        return None


# ─────────────────────────────────────────────
# Appointment Service
# ─────────────────────────────────────────────

class AppointmentService:

    async def create_appointment(
        self,
        user_id:          str,
        appointment_type: str,
        title:            str,
        date_hint:        str = "",
        doctor_name:      str = "",
        location:         str = "",
    ) -> dict:
        scheduled_at = _resolve_date(date_hint)

        doc = AppointmentDocument.build(
            user_id=user_id,
            appointment_type=appointment_type,
            title=title,
            scheduled_at=scheduled_at,
            doctor_name=doctor_name,
            location=location,
        )

        db = await get_database()
        await db[AppointmentDocument.COLLECTION].insert_one(doc)
        logger.info(f"Appointment created: {doc['reference']} for user {user_id}")

        appt_out = AppointmentDocument.to_out(doc)

        # Send Twilio confirmation using user's saved contact details
        user = await _get_user(user_id)
        if user and user.get("phone"):
            try:
                from services.twilio_service import twilio_service
                twilio_service.send_appointment_confirmation(user, appt_out)
            except Exception as e:
                logger.warning(f"Twilio confirmation failed (non-fatal): {e}")

        return appt_out

    async def get_user_appointments(self, user_id: str) -> list[dict]:
        db     = await get_database()
        cursor = db[AppointmentDocument.COLLECTION].find(
            {"user_id": user_id},
            sort=[("created_at", -1)],
        )
        docs = await cursor.to_list(length=50)
        return [AppointmentDocument.to_out(d) for d in docs]

    async def cancel_appointment(self, appointment_id: str, user_id: str) -> bool:
        db = await get_database()
        result = await db[AppointmentDocument.COLLECTION].find_one_and_update(
            {"appointment_id": appointment_id, "user_id": user_id},
            {"$set": {
                "status": "cancelled",
                "updated_at": datetime.now(tz=timezone.utc),
            }},
            return_document=True,
        )
        if result:
            appt_out = AppointmentDocument.to_out(result)
            user     = await _get_user(user_id)
            if user and user.get("phone"):
                try:
                    from services.twilio_service import twilio_service
                    twilio_service.send_appointment_cancelled(user, appt_out)
                except Exception as e:
                    logger.warning(f"Twilio cancellation SMS failed (non-fatal): {e}")
            logger.info(f"Appointment {appointment_id} cancelled")
            return True
        return False

    async def get_appointment(self, appointment_id: str) -> dict | None:
        db  = await get_database()
        doc = await db[AppointmentDocument.COLLECTION].find_one(
            {"appointment_id": appointment_id}
        )
        return AppointmentDocument.to_out(doc) if doc else None


# ─────────────────────────────────────────────
# Reminder Service  (APScheduler — every 15 min)
# ─────────────────────────────────────────────

class ReminderService:
    """
    Finds scheduled appointments that haven't received a reminder yet
    and sends Twilio notifications using each user's contact preferences.
    """

    async def dispatch_due_reminders(self) -> int:
        try:
            from services.twilio_service import twilio_service
        except Exception:
            return 0

        db     = await get_database()
        cursor = db[AppointmentDocument.COLLECTION].find(
            {"status": "scheduled", "reminder_sent": False}
        )
        docs = await cursor.to_list(length=200)

        sent = 0
        for doc in docs:
            try:
                user     = await _get_user(doc["user_id"])
                if not user or not user.get("phone"):
                    logger.warning(f"No phone for {doc['user_id']} — skipping reminder")
                    continue

                appt_out = AppointmentDocument.to_out(doc)
                ok       = twilio_service.send_appointment_reminder(user, appt_out)

                if ok:
                    await db[AppointmentDocument.COLLECTION].update_one(
                        {"appointment_id": doc["appointment_id"]},
                        {"$set": {"reminder_sent": True}},
                    )
                    sent += 1

            except Exception as e:
                logger.error(f"Reminder failed for {doc.get('appointment_id')}: {e}")

        return sent