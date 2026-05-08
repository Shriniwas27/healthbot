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

def _resolve_date(date_hint: str) -> str:
    """
    Accepts an ISO 8601 string from the LLM (e.g. '2026-05-10T09:00:00Z').
    Stores it as-is in MongoDB. Falls back to 'TBD' if missing or unparseable.
    """
    if not date_hint or date_hint.strip().lower() in ("null", "none", "tbd", ""):
        return "TBD"
    try:
        # Normalise and validate — store canonical ISO format
        dt = datetime.fromisoformat(date_hint.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        # Last resort: return as-is, _parse_scheduled_at will handle failure
        return "TBD"

# def _resolve_date(date_hint: str) -> str:
#     if not date_hint:
#         return "TBD"

#     hint = date_hint.strip()
#     hint_lower = hint.lower()
#     now = datetime.now(tz=timezone.utc)

    
#     hour = 9  
#     time_match = re.search(
#         r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b'
#         r'|(?:at|@)\s*(\d{1,2})(?::(\d{2}))?',
#         hint_lower,
#         re.IGNORECASE,
#     )
#     minute = 0
#     if time_match:
#         if time_match.group(1):
#             h = int(time_match.group(1))
#             m = int(time_match.group(2) or 0)
#             meridiem = (time_match.group(3) or "").lower()
#         else:
#             h = int(time_match.group(4))
#             m = int(time_match.group(5) or 0)
#             meridiem = ""
#         if meridiem == "pm" and h != 12: h += 12
#         elif meridiem == "am" and h == 12: h = 0
#         hour, minute = h, m

    
#     date_only = re.sub(
#         r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|(?:at|on|@)\s*(\d{1,2})(?::(\d{2}))?(?:\s*(am|pm))?',
#         '', hint, flags=re.IGNORECASE
#     )
#     date_only = re.sub(r'\b(at|on)\b', ' ', date_only, flags=re.IGNORECASE)
#     date_only = re.sub(r'\s+', ' ', date_only).strip()

#     absolute_formats = [
#         "%d %b %Y", "%d %B %Y",
#         "%Y-%m-%d",
#         "%d/%m/%Y", "%d/%m/%y",
#         "%m/%d/%Y",
#     ]
#     for fmt in absolute_formats:
#         try:
#             parsed = datetime.strptime(date_only, fmt)
#             resolved = parsed.replace(hour=hour, minute=minute, second=0,
#                                       microsecond=0, tzinfo=timezone.utc)
#             return resolved.strftime("%d %b %Y, %I:%M %p")
#         except ValueError:
#             pass

    
#     if "tomorrow"    in hint_lower: base = now + timedelta(days=1)
#     elif "today"     in hint_lower: base = now
#     elif "next week" in hint_lower: base = now + timedelta(weeks=1)
#     elif "monday"    in hint_lower: base = now + timedelta(days=(0 - now.weekday()) % 7 or 7)
#     elif "tuesday"   in hint_lower: base = now + timedelta(days=(1 - now.weekday()) % 7 or 7)
#     elif "wednesday" in hint_lower: base = now + timedelta(days=(2 - now.weekday()) % 7 or 7)
#     elif "thursday"  in hint_lower: base = now + timedelta(days=(3 - now.weekday()) % 7 or 7)
#     elif "friday"    in hint_lower: base = now + timedelta(days=(4 - now.weekday()) % 7 or 7)
#     else: base = now + timedelta(days=1)

#     resolved = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
#     return resolved.strftime("%d %b %Y, %I:%M %p")


# def _parse_scheduled_at(scheduled_at: str) -> datetime | None:
#     """Parse the stored human-readable appointment time into a UTC datetime."""
#     if not scheduled_at or scheduled_at == "TBD":
#         return None

#     try:
#         return datetime.strptime(scheduled_at, "%d %b %Y, %I:%M %p").replace(tzinfo=timezone.utc)
#     except ValueError:
#         return None
# def _parse_scheduled_at(scheduled_at: str) -> datetime | None:
#     """Parse the stored ISO 8601 appointment time into a UTC datetime."""
#     if not scheduled_at or scheduled_at == "TBD":
#         return None
#     try:
#         return datetime.fromisoformat(scheduled_at.replace("Z", "+00:00")).astimezone(timezone.utc)
#     except (ValueError, TypeError):
#         return 
# NEW
def _parse_scheduled_at(scheduled_at: str) -> datetime | None:
    if not scheduled_at or scheduled_at == "TBD":
        return None
    try:
        return datetime.fromisoformat(scheduled_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

async def _get_user(user_id: str) -> dict | None:
    """Fetch user document for notification purposes."""
    try:
        db  = await get_database()
        doc = await db["users"].find_one({"user_id": user_id})
        if not doc:
            
            return {
                "phone": user_id if user_id.startswith("+") else "",
                "notification_channel": "sms",
                "full_name": "User"
            }
        return doc
    except Exception as e:
        logger.error(f"Failed to fetch user {user_id}: {e}")
        return None




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
        result = await db[AppointmentDocument.COLLECTION].insert_one(doc)
        if not getattr(result, "inserted_id", None):
            logger.error("Failed to insert appointment into MongoDB")
            raise RuntimeError("Appointment insertion failed")

        
        saved = await db[AppointmentDocument.COLLECTION].find_one({"appointment_id": doc["appointment_id"]})
        if not saved:
            logger.error("Inserted appointment not found after insert")
            raise RuntimeError("Inserted appointment not found")

        logger.info(f"Appointment created: {saved['reference']} for user {user_id}")

        appt_out = AppointmentDocument.to_out(saved)

       
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



class ReminderService:

    async def dispatch_due_reminders(self) -> int:
        try:
            from services.twilio_service import twilio_service
        except ImportError:
            logger.error("Could not import twilio_service from services.notificationservice")
            return 0

        db = await get_database()
        
        cursor = db[AppointmentDocument.COLLECTION].find(
            {"status": "scheduled", "reminder_sent": False}
        )
        docs = await cursor.to_list(length=200)

        now = datetime.now(tz=timezone.utc)
        
        reminder_window_end = now + timedelta(minutes=15)

        sent = 0
        logger.info(f"Cron: Checking {len(docs)} appointments for due reminders... (Window: next 15 mins)")

        for doc in docs:
            try:
                scheduled_at = _parse_scheduled_at(doc.get("scheduled_at", ""))
                
                if not scheduled_at:
                    logger.warning(f"Skipping appt {doc.get('reference')} - Invalid date format: {doc.get('scheduled_at')}")
                    continue

                if scheduled_at < (now - timedelta(minutes=5)) or scheduled_at > reminder_window_end:
                    continue

                user = await _get_user(doc["user_id"])
                if not user or not user.get("phone"):
                    logger.warning(f"No phone number for user {doc['user_id']} — skipping reminder")
                    continue

                appt_out = AppointmentDocument.to_out(doc)
                logger.info(f"Triggering reminder for {doc['reference']} to {user['phone']}...")
                
                ok = twilio_service.send_appointment_reminder(user, appt_out)

                if ok:
                    await db[AppointmentDocument.COLLECTION].update_one(
                        {"appointment_id": doc["appointment_id"]},
                        {"$set": {"reminder_sent": True, "updated_at": now}},
                    )
                    sent += 1
                    logger.info(f"Reminder sent successfully for {doc['reference']}")

            except Exception as e:
                logger.error(f"Reminder loop error for {doc.get('reference')}: {e}")

        return sent