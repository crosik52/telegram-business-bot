import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from google import genai

from app.bot import commands
from app.config import Settings
from app.miniapp import routes, subscription_middleware

BOT_TOKEN = "123456:offline-admin-test-token"
ADMIN_ID = 5_123_456_789
FORMER_ADMIN_USERNAME = "old_admin"
ADMIN_PREFIX = "/app/api/admin/"
PING_PATH = ADMIN_PREFIX + "ai/ping"


def signed_init_data(user, *, bot_token=BOT_TOKEN, auth_date=None):
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "user": json.dumps(user),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("SESSION_SECRET", "offline-admin-test-session-secret")
    monkeypatch.setenv("MINIAPP_ADMIN_TELEGRAM_ID", str(ADMIN_ID))
    monkeypatch.setenv("MINIAPP_ADMIN_USERNAME", FORMER_ADMIN_USERNAME)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "offline-test-webhook-secret")

    def settings():
        return Settings(_env_file=None)

    monkeypatch.setattr(routes, "get_settings", settings)
    monkeypatch.setattr(commands, "get_settings", settings)


@pytest.fixture
def api(admin_env, monkeypatch):
    from app.main import app

    def reject_db_access():
        pytest.fail("An unauthorized request reached the database dependency")

    monkeypatch.setitem(app.dependency_overrides, routes.get_db_session, reject_db_access)
    # TestClient without a lifespan context exercises production routing without
    # registering a webhook, starting background jobs, or opening a real database.
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test-key")
    factory = Mock(spec=genai.Client)
    factory.return_value.models.generate_content.return_value = SimpleNamespace(text="OK")
    monkeypatch.setattr(genai, "Client", factory)
    return factory


@pytest.mark.parametrize("credential", ["missing", "forged", "outsider"])
def test_every_admin_route_rejects_untrusted_requests(api, gemini, credential):
    if credential == "missing":
        payload, expected_status = {}, 401
    elif credential == "forged":
        payload = {"initData": signed_init_data({"id": ADMIN_ID}, bot_token="wrong-token")}
        expected_status = 401
    else:
        payload = {
            "initData": signed_init_data({"id": ADMIN_ID + 1, "username": FORMER_ADMIN_USERNAME}),
            "ownerTelegramId": ADMIN_ID,
        }
        expected_status = 403

    registered = [
        (method, route.path)
        for route in api.app.routes
        if route.path.startswith(ADMIN_PREFIX)
        for method in route.methods
    ]
    assert ("POST", PING_PATH) in registered
    for method, path in registered:
        response = api.request(method, path, json=payload)
        assert response.status_code == expected_status, (method, path, response.text)
    gemini.assert_not_called()


@pytest.mark.parametrize(
    ("field", "user"),
    [
        ("initData", {"id": ADMIN_ID}),
        ("init_data", {"id": ADMIN_ID, "username": "renamed_admin"}),
    ],
)
def test_ai_ping_accepts_the_configured_id(api, gemini, field, user):
    response = api.post(PING_PATH, json={field: signed_init_data(user)})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "response": "OK"}
    gemini.return_value.models.generate_content.assert_called_once()


def test_verified_admin_without_username_can_update_settings(api, monkeypatch):
    session = Mock()
    session.commit = AsyncMock()
    monkeypatch.setitem(api.app.dependency_overrides, routes.get_db_session, lambda: session)
    stats_service = Mock()
    stats_service.set_owner_settings = AsyncMock(return_value=1)
    monkeypatch.setattr(routes, "StatsService", Mock(return_value=stats_service))

    response = api.post(ADMIN_PREFIX + "settings", json={
        "initData": signed_init_data({"id": ADMIN_ID}),
        "ownerTelegramId": ADMIN_ID + 1,
        "isBlocked": True,
    })

    assert response.status_code == 200
    stats_service.set_owner_settings.assert_awaited_once_with(
        owner_telegram_id=ADMIN_ID + 1, notifications_enabled=None, is_blocked=True,
    )
    session.commit.assert_awaited_once()
    assert session.add.call_args.args[0].admin_username is None


@pytest.mark.parametrize(
    "bad_id",
    [None, "", "  ", "0", "-1", "1.0", "1e3", "1_000", "+1", "@old_admin", "٤٢", str(2**52)],
)
def test_invalid_or_absent_admin_setting_disables_access(api, gemini, monkeypatch, bad_id):
    if bad_id is None:
        monkeypatch.delenv("MINIAPP_ADMIN_TELEGRAM_ID")
    else:
        monkeypatch.setenv("MINIAPP_ADMIN_TELEGRAM_ID", bad_id)
    init_data = signed_init_data({"id": ADMIN_ID, "username": FORMER_ADMIN_USERNAME})

    assert Settings(_env_file=None).miniapp_admin_telegram_id is None
    assert api.post(PING_PATH, json={"initData": init_data}).status_code == 403
    gemini.assert_not_called()


@pytest.mark.parametrize("user_id", [True, 1.0, "1", None])
def test_signed_user_id_must_be_an_integer(api, gemini, monkeypatch, user_id):
    monkeypatch.setenv("MINIAPP_ADMIN_TELEGRAM_ID", "1")
    init_data = signed_init_data({"id": user_id, "username": FORMER_ADMIN_USERNAME})

    assert api.post(PING_PATH, json={"initData": init_data}).status_code == 403
    gemini.assert_not_called()


def test_empty_bot_token_cannot_be_used_to_sign_admin_requests(api, gemini, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    init_data = signed_init_data({"id": ADMIN_ID}, bot_token="")

    assert api.post(PING_PATH, json={"initData": init_data}).status_code == 401
    gemini.assert_not_called()


def test_ai_ping_rejects_stale_init_data_and_malformed_hash(api, gemini):
    stale = signed_init_data({"id": ADMIN_ID}, auth_date=int(time.time()) - 25 * 3600)
    assert api.post(PING_PATH, json={"initData": stale}).status_code == 401
    assert api.post(PING_PATH, json={"initData": "hash=%C3%A9"}).status_code == 401
    gemini.assert_not_called()


def test_privileged_bot_commands_require_admin_id_and_webhook_secret(admin_env, monkeypatch):
    assert commands._is_admin(ADMIN_ID)
    assert not commands._is_admin(ADMIN_ID + 1)

    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    assert not commands._is_admin(ADMIN_ID)


def test_old_username_cannot_preview_a_locked_giveaway(api, monkeypatch):
    async def session():
        yield object()

    config = SimpleNamespace(opens_at=None, is_visible_to_all=False)
    repository = Mock()
    repository.get_config = AsyncMock(return_value=config)
    monkeypatch.setattr(routes, "GiveawayRepository", Mock(return_value=repository))
    monkeypatch.setattr(subscription_middleware, "_subscription_status", AsyncMock(return_value=True))
    monkeypatch.setitem(api.app.dependency_overrides, routes.get_db_session, session)

    init_data = signed_init_data({"id": ADMIN_ID + 1, "username": FORMER_ADMIN_USERNAME})
    response = api.post("/app/api/giveaway", json={"initData": init_data})

    assert response.status_code == 200
    assert response.json() == {"locked": True, "opens_at": None}
    repository.get_top_referrers.assert_not_called()


@pytest.mark.parametrize("upstream_error", [False, True])
def test_public_avatar_never_returns_the_bot_token(api, monkeypatch, upstream_error):
    file_id = "stored-photo-file-id"
    jpeg = b"\xff\xd8test-avatar\xff\xd9"

    async def session():
        row = SimpleNamespace(photo_file_id=file_id)
        result = Mock()
        result.scalar_one_or_none.return_value = row
        yield SimpleNamespace(execute=AsyncMock(return_value=result))

    async def download(requested_file_id, *, destination, timeout):
        assert requested_file_id == file_id
        if upstream_error:
            raise RuntimeError(f"Failed to download file with token {BOT_TOKEN}")
        destination.write(jpeg)

    bot = SimpleNamespace(download=AsyncMock(side_effect=download))
    monkeypatch.setattr(routes, "get_bot", lambda settings: bot)
    monkeypatch.setitem(api.app.dependency_overrides, routes.get_db_session, session)

    response = api.get(f"/app/api/avatar/{ADMIN_ID}", follow_redirects=False)

    assert "location" not in response.headers
    assert BOT_TOKEN.encode() not in response.content
    if upstream_error:
        assert response.status_code == 404
        assert response.json()["detail"] == "Avatar unavailable"
    else:
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == jpeg
