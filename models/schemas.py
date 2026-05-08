"""
Pydantic Schemas
================
Request bodies, response models, and shared enums used across
the FastAPI routers and service layer.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Language(str, Enum):
    ENGLISH    = "en"
    HINDI      = "hi"
    MARATHI    = "mr"
    SPANISH    = "es"
    FRENCH     = "fr"
    ARABIC     = "ar"
    TAMIL      = "ta"


class AppointmentType(str, Enum):
    DOCTOR    = "doctor"
    VACCINE   = "vaccine"
    LAB_TEST  = "lab_test"


class AppointmentStatus(str, Enum):
    SCHEDULED  = "scheduled"
    CONFIRMED  = "confirmed"
    CANCELLED  = "cancelled"
    COMPLETED  = "completed"


class Intent(str, Enum):
    SYMPTOM_QUERY  = "symptom_query"
    SCHEDULING     = "scheduling"
    GENERAL_HEALTH = "general_health"
    OFF_TOPIC      = "off_topic"
    ERROR          = "error"




class ChatRequest(BaseModel):
    message:    str            = Field(..., min_length=1, max_length=2000)
    session_id: str | None     = None
    user_id:    str            = "anonymous"
    language:   Language       = Language.ENGLISH


class ChatResponse(BaseModel):
    reply:               str
    session_id:          str
    intent:              Intent | None          = None
    follow_up_questions: list[str]              = []
    probable_conditions: list[dict[str, Any]]  = []
    appointment_created: dict[str, Any] | None = None


class MessageOut(BaseModel):
    role:      str
    content:   str
    timestamp: float
    metadata:  dict[str, Any] = {}


class AppointmentCreate(BaseModel):
    appointment_type: AppointmentType = AppointmentType.DOCTOR
    title:            str             = Field(..., min_length=1, max_length=200)
    date_hint:        str             = ""          # natural language date, e.g. "tomorrow 3pm"
    doctor_name:      str             = ""
    location:         str             = ""


class AppointmentOut(BaseModel):
    id:               str
    user_id:          str
    appointment_type: AppointmentType
    title:            str
    scheduled_at:     str                         
    doctor_name:      str
    location:         str
    status:           AppointmentStatus
    reference:        str                          
    reminder_sent:    bool = False
    created_at:       datetime


class AppointmentCancel(BaseModel):
    appointment_id: str


class ProbableCondition(BaseModel):
    name:           str
    confidence:     float = Field(..., ge=0.0, le=1.0)
    recommendation: str
    disclaimer:     str = "This is not a medical diagnosis. Please consult a doctor."



class HealthCheck(BaseModel):
    status:                 str
    active_sessions:        int
    adk_session_service_id: int
    agents:                 list[str]