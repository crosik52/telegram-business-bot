"""Dashboard authentication service (single-admin password auth)."""

from __future__ import annotations

import hmac

from app.config import Settings


class AuthService:
    """Validates dashboard credentials against configured admin secrets."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def verify_credentials(self, username: str, password: str) -> bool:
        username_ok = hmac.compare_digest(
            username.strip(), self._settings.dashboard_admin_username
        )
        password_ok = hmac.compare_digest(
            password, self._settings.dashboard_admin_password
        )
        return username_ok and password_ok
