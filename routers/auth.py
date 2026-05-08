"""
Auth Router
===========
GET  /register  → serve registration page
POST /register  → create user, send Twilio welcome SMS
GET  /login     → serve login page
POST /login     → authenticate, set session cookie
GET  /logout    → clear session cookie
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, Field

from services.user_service import user_service
from services.twilio_service import twilio_service
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="static")


class RegisterRequest(BaseModel):
    full_name:            str            = Field(..., min_length=2)
    email:                EmailStr
    password:             str            = Field(..., min_length=8)
    phone:                str            = Field(..., min_length=10, max_length=15) 
    age:                  int | None     = None
    gender:               str | None     = None
    blood_group:          str | None     = None
    existing_conditions:  list[str]      = []
    notification_channel: str            = "sms"


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="register.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


# ── API endpoints ──────────────────────────────────────────

@router.post("/register")
async def register(request: Request):
    """Accept JSON or form-encoded registration payloads for robustness."""
    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        payload = RegisterRequest.parse_obj(data)

        user = await user_service.register(
            full_name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
            age=payload.age,
            gender=payload.gender,
            blood_group=payload.blood_group,
            existing_conditions=payload.existing_conditions,
            notification_channel=payload.notification_channel,
        )

        
        twilio_service.send_welcome(user)

        return RedirectResponse(url="/login?registered=true", status_code=303)

    except ValueError as e:
        return JSONResponse(status_code=409, content={"detail": str(e)})
    except Exception as e:
        logger.exception("Registration error")
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@router.post("/login")
async def login(request: Request):
    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        payload = LoginRequest.parse_obj(data)
        user = await user_service.login(payload.email, payload.password)

        if not user:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid email or password."}
            )

        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie("user_id", user["user_id"], httponly=True, samesite="lax")
        return resp

    except Exception as e:
        logger.exception("Login error")
        return JSONResponse(status_code=400, content={"detail": "Invalid request payload."})


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("user_id")
    response.delete_cookie("session_id")
    return response