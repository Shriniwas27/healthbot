"""
SNS Notification Service 
=============================================================
"""

from __future__ import annotations

import os
from typing import Dict

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from utils.logger import get_logger

logger = get_logger(__name__)


class TwilioService:
    """SNS-backed notification service"""

    def __init__(self) -> None:
        self._ready = False
        self._client = None
        self._region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        self._sender_id = os.getenv("SNS_SENDER_ID", "")

        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

        if not access_key or not secret_key or not self._region:
            logger.warning("AWS SNS credentials incomplete — notifications disabled")
            return

        try:
            self._client = boto3.client(
                "sns",
                region_name=self._region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            _ = self._client.get_sms_attributes()
            self._ready = True
            logger.info(" SNS client ready.")
        except (BotoCoreError, ClientError) as e:
            logger.exception("SNS client init failed: %s", e)
            self._ready = False

    def _send_sms(self, phone: str, body: str) -> bool:
        if not self._ready:
            logger.warning("SNS not ready — skipping send to %s", phone)
            return False

        attrs: Dict[str, Dict[str, str]] = {
            "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}
        }
        if self._sender_id:
            attrs["AWS.SNS.SMS.SenderID"] = {"DataType": "String", "StringValue": self._sender_id}

        try:
            resp = self._client.publish(PhoneNumber=phone, Message=body, MessageAttributes=attrs)
            msg_id = resp.get("MessageId")
            logger.info("SMS sent to %s — MessageId: %s", phone, msg_id)
            return bool(msg_id)
        except (BotoCoreError, ClientError) as e:
            logger.error("SNS send failed to %s: %s", phone, e)
            return False


    def send_welcome(self, user: dict) -> bool:
        phone = user.get("phone", "")
        name = user.get("full_name", "")
        body = f"Welcome {name}! Your account is ready. — Healthbot"
        return self._send_sms(phone, body)

    def send_appointment_confirmation(self, user: dict, appointment: dict) -> bool:
        phone = user.get("phone", "")
        name = user.get("full_name", "")
        body = (
            f"Hi {name}, your appointment is confirmed:\n"
            f"{appointment.get('title', 'Appointment')} — {appointment.get('scheduled_at', 'TBD')}\n"
            f"Ref: {appointment.get('reference', '')}"
        )
        return self._send_sms(phone, body)

    def send_appointment_reminder(self, user: dict, appointment: dict) -> bool:
        phone = user.get("phone", "")
        name = user.get("full_name", "")
        body = f"Reminder: {appointment.get('title', 'Appointment')} at {appointment.get('scheduled_at','TBD')}"
        return self._send_sms(phone, body)

    def send_appointment_cancelled(self, user: dict, appointment: dict) -> bool:
        phone = user.get("phone", "")
        name = user.get("full_name", "")
        body = f"Hello {name}, your appointment (Ref: {appointment.get('reference','')}) has been cancelled."
        return self._send_sms(phone, body)

    def send_otp(self, phone: str, otp: str) -> bool:
        body = f"Your verification code is: {otp}"
        return self._send_sms(phone, body)


twilio_service = TwilioService()