"""Session-based authentication guard for dashboard routes."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

SESSION_USER_KEY = "dashboard_user"


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get(SESSION_USER_KEY))


def login_user(request: Request, username: str) -> None:
    request.session[SESSION_USER_KEY] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def require_login(request: Request) -> RedirectResponse | None:
    """Returns a redirect response if the user is not authenticated, else None."""

    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    return None
