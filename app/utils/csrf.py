"""Lightweight CSRF protection for the session-based dashboard forms.

Uses the double-submit cookie pattern: a random token is stored in the
signed session and must be echoed back as a hidden form field on every
state-changing POST request.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

CSRF_SESSION_KEY = "csrf_token"


def get_or_create_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(request: Request, submitted_token: str | None) -> None:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not submitted_token or not secrets.compare_digest(
        expected, submitted_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token"
        )
