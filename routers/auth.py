"""
Auth Router
===========
GET  /register  → serve registration page
POST /register  → create user, send Twilio welcome SMS
GET  /login     → serve login page
POST /login     → authenticate, set session cookie
GET  /logout    → clear session cookie
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, Field

from services.user_service import user_service
from services.twilio_service import twilio_service
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="static")


# ── Request schemas ────────────────────────────────────────

class RegisterRequest(BaseModel):
    full_name:            str            = Field(..., min_length=2)
    email:                str
    password:             str            = Field(..., min_length=8)
    phone:                str            = Field(..., min_length=10)
    age:                  int | None     = None
    gender:               str | None     = None
    blood_group:          str | None     = None
    existing_conditions:  list[str]      = []
    preferred_language:   str            = "en"
    notification_channel: str            = "sms"


class LoginRequest(BaseModel):
    email:    str
    password: str


# ── Pages ──────────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="register.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


# ── API endpoints ──────────────────────────────────────────

@router.post("/register")
async def register(payload: RegisterRequest):
    try:
        user = await user_service.register(
            full_name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
            age=payload.age,
            gender=payload.gender,
            blood_group=payload.blood_group,
            existing_conditions=payload.existing_conditions,
            preferred_language=payload.preferred_language,
            notification_channel=payload.notification_channel,
        )

        # Send Twilio welcome message
        twilio_service.send_welcome(user)

        return JSONResponse(
            status_code=201,
            content={
                "message": "Account created successfully.",
                "user_id": user["user_id"],
                "full_name": user["full_name"],
            }
        )

    except ValueError as e:
        return JSONResponse(status_code=409, content={"detail": str(e)})
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@router.post("/login")
async def login(payload: LoginRequest, response: Response):
    user = await user_service.login(payload.email, payload.password)

    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid email or password."}
        )

    # Set cookies so the chat router picks up user identity
    response = JSONResponse(
        status_code=200,
        content={
            "message": "Login successful.",
            "user_id": user["user_id"],
            "full_name": user["full_name"],
            "preferred_language": user["preferred_language"],
        }
    )
    response.set_cookie("user_id",  user["user_id"],              httponly=True, samesite="lax")
    response.set_cookie("language", user["preferred_language"],   httponly=True, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = JSONResponse(content={"message": "Logged out."})
    response.delete_cookie("user_id")
    response.delete_cookie("session_id")
    response.delete_cookie("language")
    return response