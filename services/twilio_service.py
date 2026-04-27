"""
Twilio Notification Service
============================
Sends SMS and WhatsApp messages for:
  - Appointment reminders
  - Appointment confirmations
  - Registration welcome messages

Supports both SMS and WhatsApp based on user's notification_channel preference.
"""

from __future__ import annotations

from utils.logger import get_logger

logger = get_logger(__name__)


class TwilioService:
    """
    Wraps Twilio REST client.
    Gracefully degrades if credentials are missing — app keeps running.
    """

    def __init__(self):
        self._ready       = False
        self._client      = None
        self._sms_from    = ""
        self._wa_from     = ""   # WhatsApp sender: "whatsapp:+14155238886"
        self._setup()

    def _setup(self) -> None:
        try:
            from config import get_settings
            s = get_settings()

            if not all([s.twilio_account_sid, s.twilio_auth_token, s.twilio_phone_number]):
                logger.warning(
                    "Twilio credentials incomplete — notifications disabled. "
                    "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER in .env"
                )
                return

            from twilio.rest import Client
            self._client   = Client(s.twilio_account_sid, s.twilio_auth_token)
            self._sms_from = s.twilio_phone_number
            self._wa_from  = f"whatsapp:{s.twilio_whatsapp_number or s.twilio_phone_number}"
            self._ready    = True
            logger.info("✅ Twilio client ready.")
        except ImportError:
            logger.warning("twilio package not installed. Run: pip install twilio")
        except Exception as e:
            logger.error(f"Twilio setup error: {e}")

    # ── Core send methods ──────────────────────────────────

    def _send_sms(self, to: str, body: str) -> bool:
        """Send a plain SMS. `to` must be E.164 format e.g. +919876543210"""
        if not self._ready:
            logger.warning(f"[TWILIO DISABLED] SMS to {to}: {body}")
            return False
        try:
            msg = self._client.messages.create(
                body=body, from_=self._sms_from, to=to
            )
            logger.info(f"SMS sent to {to} — SID: {msg.sid}")
            return True
        except Exception as e:
            logger.error(f"SMS failed to {to}: {e}")
            return False

    def _send_whatsapp(self, to: str, body: str) -> bool:
        """Send a WhatsApp message. `to` must be E.164 format."""
        if not self._ready:
            logger.warning(f"[TWILIO DISABLED] WhatsApp to {to}: {body}")
            return False
        try:
            wa_to = f"whatsapp:{to}" if not to.startswith("whatsapp:") else to
            msg = self._client.messages.create(
                body=body, from_=self._wa_from, to=wa_to
            )
            logger.info(f"WhatsApp sent to {to} — SID: {msg.sid}")
            return True
        except Exception as e:
            logger.error(f"WhatsApp failed to {to}: {e}")
            return False

    def _send(self, to: str, body: str, channel: str = "sms") -> bool:
        """Route to SMS or WhatsApp based on user preference."""
        if channel == "whatsapp":
            return self._send_whatsapp(to, body)
        elif channel == "both":
            sms_ok = self._send_sms(to, body)
            wa_ok  = self._send_whatsapp(to, body)
            return sms_ok or wa_ok
        else:
            return self._send_sms(to, body)

    # ── High-level notification methods ───────────────────

    def send_welcome(self, user: dict) -> bool:
        """Send welcome message after successful registration."""
        name    = user.get("full_name", "there")
        phone   = user.get("phone", "")
        channel = user.get("notification_channel", "sms")
        lang    = user.get("preferred_language", "en")

        messages = {
            "en": (
                f"👋 Welcome to Health Chatbot, {name}!\n"
                f"You're all set. Chat with us anytime for health guidance, "
                f"symptom checks, and appointment scheduling.\n"
                f"Reply STOP to unsubscribe."
            ),
            "hi": (
                f"👋 स्वास्थ्य चैटबॉट में आपका स्वागत है, {name}!\n"
                f"आप तैयार हैं। स्वास्थ्य मार्गदर्शन के लिए कभी भी चैट करें।"
            ),
            "mr": (
                f"👋 हेल्थ चॅटबॉटमध्ये आपले स्वागत आहे, {name}!\n"
                f"आरोग्य मार्गदर्शनासाठी कधीही चॅट करा।"
            ),
        }
        body = messages.get(lang, messages["en"])
        return self._send(phone, body, channel)

    def send_appointment_confirmation(self, user: dict, appointment: dict) -> bool:
        """Send confirmation when an appointment is created."""
        phone   = user.get("phone", "")
        channel = user.get("notification_channel", "sms")
        name    = user.get("full_name", "")

        body = (
            f"✅ Appointment Confirmed!\n"
            f"Hi {name}, your appointment has been booked:\n"
            f"📋 {appointment.get('title', 'Appointment')}\n"
            f"📅 {appointment.get('scheduled_at', 'TBD')}\n"
            f"📍 {appointment.get('location') or 'TBD'}\n"
            f"👨‍⚕️ {appointment.get('doctor_name') or 'TBD'}\n"
            f"🔖 Ref: {appointment.get('reference', '')}\n\n"
            f"Reply CONFIRM to confirm or CANCEL to cancel."
        )
        return self._send(phone, body, channel)

    def send_appointment_reminder(self, user: dict, appointment: dict) -> bool:
        """Send reminder 1 hour before appointment."""
        phone   = user.get("phone", "")
        channel = user.get("notification_channel", "sms")
        name    = user.get("full_name", "")

        body = (
            f"⏰ Reminder!\n"
            f"Hi {name}, your appointment is coming up soon:\n"
            f"📋 {appointment.get('title', 'Appointment')}\n"
            f"📅 {appointment.get('scheduled_at', 'TBD')}\n"
            f"📍 {appointment.get('location') or 'TBD'}\n"
            f"🔖 Ref: {appointment.get('reference', '')}\n\n"
            f"Reply CONFIRM to confirm or CANCEL to cancel."
        )
        return self._send(phone, body, channel)

    def send_appointment_cancelled(self, user: dict, appointment: dict) -> bool:
        """Notify user when an appointment is cancelled."""
        phone   = user.get("phone", "")
        channel = user.get("notification_channel", "sms")

        body = (
            f"❌ Appointment Cancelled\n"
            f"Your appointment '{appointment.get('title')}' "
            f"(Ref: {appointment.get('reference', '')}) has been cancelled.\n"
            f"Book a new one anytime via the Health Chatbot."
        )
        return self._send(phone, body, channel)

    def send_otp(self, phone: str, otp: str, channel: str = "sms") -> bool:
        """Send OTP for phone verification."""
        body = (
            f"🔐 Your Health Chatbot verification code is: {otp}\n"
            f"Valid for 10 minutes. Do not share this with anyone."
        )
        return self._send(phone, body, channel)


# Singleton
twilio_service = TwilioService()