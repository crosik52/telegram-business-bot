"""Offline candidate-image compatibility probe; no lifespan, database or API calls.

Run with the image's Python from its application working directory. The caller
must also use --network none and a read-only, resource-bounded container.
Only stdlib test helpers are used; installed application dependencies are real.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import importlib
import io
import json
import logging
import os
import secrets
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.dont_write_bytecode = True
logging.disable(logging.CRITICAL)
OS_KEYS = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE",
           "APPDATA", "LOCALAPPDATA", "PATHEXT", "LD_LIBRARY_PATH"}
preserved = {key: value for key, value in os.environ.items() if key.upper() in OS_KEYS}
os.environ.clear()
os.environ.update(preserved)
os.environ.update({
    "TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35,
    "SESSION_SECRET": secrets.token_hex(32),
    "DATABASE_URL": "postgresql+asyncpg://synthetic@127.0.0.1:1/never_connect",
    "DATABASE_AUTO_INIT": "false", "ENVIRONMENT": "test", "WEBHOOK_BASE_URL": "",
    "TELEGRAM_WEBHOOK_SECRET": "", "MINIAPP_ADMIN_ID": "999",
    "GEMINI_API_KEY": "", "PYTHONDONTWRITEBYTECODE": "1",
})

PYTHON_FILES = (
    "app/services/channel_subscription_service.py", "app/models/required_channel.py",
    "app/miniapp/routes.py", "app/miniapp/subscription_middleware.py", "app/bot/commands.py",
    "app/business/handlers.py", "app/business/commands.py", "app/business/dispatcher.py",
    "app/middlewares/channel_gate.py",
)
HTML_FILES = ("app/miniapp/templates/admin.html", "app/miniapp/templates/miniapp.html")
results: dict[str, bool] = {}
stage = "bootstrap"
compiled = 0
parsed = 0


class ProbeFailure(RuntimeError):
    pass


def check(name: str, condition: bool) -> None:
    global stage
    stage = name
    results[name] = bool(condition)
    if not condition:
        raise ProbeFailure()


def source_root() -> Path:
    candidates = [Path.cwd(), Path("/app")]
    if len(Path(__file__).resolve().parents) > 3:
        candidates.append(Path(__file__).resolve().parents[3])
    for candidate in candidates:
        if all((candidate / name).is_file() for name in PYTHON_FILES + HTML_FILES):
            return candidate.resolve()
    raise ProbeFailure()


def request(method: str = "GET", *, query: str = "", body: bytes = b"", path: str = "/app/api/synthetic"):
    from starlette.requests import Request

    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
             "root_path": "", "headers": [], "server": ("synthetic", 80), "client": ("synthetic", 1)}
    return Request(scope, receive)


def fake_bot(*, premium: bool = False, status: str = "member", boosts=()):
    member = SimpleNamespace(status=status, is_member=True,
                             user=SimpleNamespace(id=42, is_premium=premium))
    return SimpleNamespace(id=900, get_chat_member=AsyncMock(return_value=member),
                           get_user_chat_boosts=AsyncMock(return_value=SimpleNamespace(boosts=list(boosts))),
                           get_chat=AsyncMock(return_value=SimpleNamespace(type="channel")),
                           send_message=AsyncMock())


async def async_checks(routes, dispatcher, gate, http_gate, bot_gate, channel_type) -> None:
    from aiogram.types import CallbackQuery, User
    from starlette.responses import Response

    gate._CHECK_SLOTS = asyncio.Semaphore(4)
    channel = channel_type(id=1, channel_username="synthetic_channel", channel_title="Synthetic channel",
                           is_active=True, require_premium_boost=True, channel_join_url=None)
    pending_before = set(asyncio.all_tasks())
    try:
        gate.clear_gate_cache()
        api = fake_bot(premium=False)
        check("nonpremium_no_boost_required", await gate.get_unsubscribed_channels(api, 42, [channel]) == []
              and api.get_user_chat_boosts.await_count == 0)

        gate.clear_gate_cache()
        api = fake_bot(premium=True)
        missing = await gate.get_unsubscribed_channels(api, 42, [channel])
        check("premium_requires_one_boost", len(missing) == 1 and missing[0].needs_boost
              and not missing[0].needs_subscription and api.get_user_chat_boosts.await_count == 1)
        item = missing[0].as_dict()
        check("gate_payload_fields", item["needs_boost"] is True and item["needs_subscription"] is False
              and item["boost_url"] == "https://t.me/boost/synthetic_channel")

        gate.clear_gate_cache()
        active_boost = SimpleNamespace(boost_id="synthetic", expiration_date=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
                                       source=SimpleNamespace(user=SimpleNamespace(id=42), source="premium"))
        api = fake_bot(premium=True, boosts=[active_boost])
        check("premium_active_boost_accepted", await gate.get_unsubscribed_channels(api, 42, [channel]) == [])

        gate.clear_gate_cache()
        api = fake_bot(premium=False, status="left")
        missing = await gate.get_unsubscribed_channels(api, 42, [channel])
        check("membership_required", len(missing) == 1 and missing[0].needs_subscription)

        gate.clear_gate_cache()
        api = fake_bot()
        api.get_chat_member.side_effect = OSError()
        unavailable = False
        try:
            await gate.get_unsubscribed_channels(api, 42, [channel])
        except gate.ChannelCheckUnavailable:
            unavailable = True
        check("service_error_fails_closed", unavailable)

        access = []
        for status in ("administrator", "member"):
            access.append(await gate.check_bot_access(fake_bot(status=status), channel))
        api = fake_bot()
        api.get_chat_member.side_effect = OSError()
        access.append(await gate.check_bot_access(api, channel))
        check("bot_access_three_states", access == [True, False, None])

        fields = {"initData": "synthetic-init", "username": "synthetic_channel", "joinUrl": None,
                  "requirePremiumBoost": True, "isActive": False}
        add = routes.AdminChannelAddRequest(**fields)
        update = routes.AdminChannelUpdateRequest(**fields, channelId=1)
        action = routes.AdminChannelActionRequest(initData="synthetic-init", channelId=1)
        check("admin_request_fields", add.require_premium_boost is True and add.is_active is False
              and update.channel_id == action.channel_id == 1 and add.join_url is None)
        rejected = False
        try:
            routes.AdminChannelAddRequest(**{**fields, "requirePremiumBoost": "false"})
        except Exception as error:
            rejected = type(error).__name__ == "ValidationError"
        check("admin_strict_booleans", rejected)
        check("admin_response_fields", routes._channel_admin_dict(channel, None)["bot_has_access"] is None
              and routes._channel_admin_dict(channel, True)["require_premium_boost"] is True)

        repository = SimpleNamespace(get_all=AsyncMock(return_value=[]), get_active=AsyncMock(return_value=[channel]))
        api = fake_bot(status="administrator")
        with patch("app.repositories.channel_repository.ChannelRepository", return_value=repository), patch.object(routes, "get_bot", return_value=api):
            prepared = await routes._prepare_channel(routes.AdminChannelAddRequest(**{**fields, "isActive": True}), MagicMock())
        check("admin_prepare_api_compatible", prepared.channel_username == "synthetic_channel"
              and prepared.require_premium_boost is True and api.get_chat.await_count == 1)

        extracted = []
        for key in ("initData", "init_data"):
            extracted.append(await http_gate._extract_init_data(request(query=key + "=synthetic-init")))
        for key in ("initData", "init_data", "resolved_init"):
            extracted.append(await http_gate._extract_init_data(request("POST", body=json.dumps({key: "synthetic-init"}).encode())))
        check("gate_extract_supported", extracted == ["synthetic-init"] * 5)

        middleware = http_gate.SubscriptionGateMiddleware(AsyncMock())
        for status, expected in ((False, 403), (None, 503), (True, 204)):
            next_handler = AsyncMock(return_value=Response(status_code=204))
            with patch.object(http_gate, "_subscription_status", AsyncMock(return_value=status)), patch("app.miniapp.auth.verify_init_data", return_value={"id": 42, "is_premium": True}):
                response = await middleware.dispatch(request(query="initData=synthetic-init"), next_handler)
            check("http_gate_" + str(expected), response.status_code == expected and next_handler.await_count == int(status is True))
        next_handler = AsyncMock(return_value=Response(status_code=204))
        with patch("app.miniapp.auth.verify_init_data", return_value=None):
            invalid = await middleware.dispatch(request(query="initData=invalid-synthetic"), next_handler)
        check("invalid_auth_rejected", invalid.status_code == 401 and next_handler.await_count == 0)

        callback = CallbackQuery(id="synthetic", from_user=User(id=42, is_bot=False, first_name="Synthetic"),
                                 chat_instance="synthetic", data="menu_main")
        api = fake_bot()
        handler = AsyncMock()
        with patch("app.bot.commands._check_channel_gate", AsyncMock(return_value=[gate.ChannelRequirement(channel, False, True)])), patch.object(CallbackQuery, "answer", AsyncMock()):
            await bot_gate.BotFeatureGateMiddleware()(handler, callback, {"bot": api})
        check("bot_callback_gate_compatible", handler.await_count == 0 and api.send_message.await_count == 1)

        registered = dispatcher.get_dispatcher()
        check("dispatcher_registration_compatible", registered is not None and len(registered.sub_routers) >= 2)
        check("no_bot_client_created", dispatcher._bot is None)
        check("no_pending_service_tasks", not gate._PENDING_CHECKS)
    finally:
        extra = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and task not in pending_before]
        for task in extra:
            task.cancel()
        if extra:
            await asyncio.gather(*extra, return_exceptions=True)
        gate.clear_gate_cache()
        routes._sub_cache.clear()


def run() -> None:
    global stage, compiled, parsed
    root = source_root()
    sys.path.insert(0, str(root))
    stage = "compile_python"
    for name in PYTHON_FILES:
        compile((root / name).read_text(encoding="utf8"), name, "exec")
        compiled += 1
    check("python_sources_compile", compiled == len(PYTHON_FILES))

    # Deny synchronous import-time connections. The coroutine checks use mocks;
    # the caller's --network none is the independent external network boundary.
    stage = "import_config"
    with patch("socket.socket.connect", side_effect=ProbeFailure()):
        config = importlib.import_module("app.config")
        config.Settings.model_config["env_file"] = None
        if hasattr(config, "DownloadCookieSettings"):
            config.DownloadCookieSettings.model_config["env_file"] = None
        config.get_settings.cache_clear()
        settings = config.get_settings()
        check("synthetic_settings_only", settings.database_auto_init is False and settings.webhook_base_url == "")
        stage = "import_routes"
        routes = importlib.import_module("app.miniapp.routes")
        check("routes_import", routes.router is not None)
        stage = "import_dispatcher"
        dispatcher = importlib.import_module("app.business.dispatcher")
        check("dispatcher_import", callable(dispatcher.get_dispatcher))
        stage = "import_gate_modules"
        http_gate = importlib.import_module("app.miniapp.subscription_middleware")
        bot_gate = importlib.import_module("app.middlewares.channel_gate")
        gate = importlib.import_module("app.services.channel_subscription_service")
        channel_type = importlib.import_module("app.models.required_channel").RequiredChannel
        check("middleware_imports", callable(http_gate.SubscriptionGateMiddleware) and callable(bot_gate.BotFeatureGateMiddleware))
        stage = "import_asgi_app"
        main_app = importlib.import_module("app.main").app
        check("asgi_routes_registered", {"/health", "/webhook", "/app", "/app/admin"} <= {route.path for route in main_app.routes})
    stage = "parse_templates"
    for name in HTML_FILES:
        routes.templates.env.parse((root / name).read_text(encoding="utf8"))
        parsed += 1
    check("templates_parse", parsed == len(HTML_FILES))
    expected_paths = {"/app/api/admin/channels", "/app/api/admin/channels/add", "/app/api/admin/channels/update",
                      "/app/api/admin/channels/toggle", "/app/api/admin/channels/delete"}
    check("admin_routes_registered", expected_paths <= {route.path for route in routes.router.routes})
    check("channel_model_fields", channel_type.__table__.c.require_premium_boost.nullable is False
          and channel_type.__table__.c.channel_join_url.type.length == 512)
    stage = "async_synthetic_checks"
    asyncio.run(async_checks(routes, dispatcher, gate, http_gate, bot_gate, channel_type))


if __name__ == "__main__":
    error_type = None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            with tempfile.TemporaryDirectory(prefix="synthetic-runtime-probe-") as cache:
                os.environ["MPLCONFIGDIR"] = cache
                run()
        except BaseException as error:
            error_type = type(error).__name__
    output = {"ok": error_type is None, "checked_source_files": compiled + parsed,
              "compiled_python": compiled, "parsed_templates": parsed,
              "passed": sum(results.values()), "cases": results,
              "network_calls": False, "database_calls": False, "lifespan_started": False}
    if error_type is not None:
        output.update({"stage": stage, "error_type": error_type})
    print(json.dumps(output, sort_keys=True))
    raise SystemExit(int(error_type is not None))
