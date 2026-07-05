"""Login / logout routes for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.dashboard.security import is_authenticated, login_user, logout_user
from app.logging_config import get_logger
from app.services.auth_service import AuthService
from app.utils.csrf import get_or_create_csrf_token, validate_csrf_token

logger = get_logger(__name__)
router = APIRouter(tags=["dashboard-auth"])
templates = Jinja2Templates(directory="app/dashboard/templates")


@router.get("/login", response_model=None)
async def login_page(request: Request) -> HTMLResponse | RedirectResponse:
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    csrf_token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"authenticated": False, "csrf_token": csrf_token, "error": None},
    )


@router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    validate_csrf_token(request, csrf_token)

    auth_service = AuthService(settings)
    if not auth_service.verify_credentials(username, password):
        logger.warning("Failed dashboard login attempt for username=%s", username)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "authenticated": False,
                "csrf_token": get_or_create_csrf_token(request),
                "error": "Invalid username or password.",
            },
            status_code=401,
        )

    login_user(request, username)
    logger.info("Dashboard login successful username=%s", username)
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout_submit(request: Request) -> RedirectResponse:
    logout_user(request)
    return RedirectResponse(url="/login", status_code=303)
