"""Scoped reply-triggered protected-media save; ordinary updates always use the inbox."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import Message, Update

from app.logging_config import get_logger
from app.services import media_timer_probe as timer
from app.services import media_upload_probe as shared
from app.services.media_copy_probe import CopyConfig, valid_file_id
from app.services.media_download_probe import valid_path
from app.services.media_save_policy_service import AppliedPolicy, policy_service

logger = get_logger(__name__)
PREFIX = "MEDIA_REPLY_SAVE_"
CASES = frozenset({"protected_photo", "protected_video"})
STATUSES = shared.STATUSES | {
    "connection_unverified", "dedup_saturated", "duplicate", "quota", "cooldown", "expired",
    "owner_capacity",
    "policy_denied",
}
STAGES = shared.STAGES
MAX_BYTES, TOTAL_TIMEOUT = shared.MAX_BYTES, shared.TOTAL_TIMEOUT
MAX_ATTEMPTS = 4
CONSENT_ATTEMPTS_PER_HOUR = 20
CONSENT_DEDUP_LIMIT = 4096
CONSENT_WINDOW_SECONDS = 3600
CONSENT_OWNER_LIMIT = 256
MAX_LOGS = 32
COOLDOWN_SECONDS = 35
CAPTION = "Save Log: сохранённое медиа."


@dataclass(frozen=True)
class ReplySaveConfig(CopyConfig):
    extra_chat_id: int | None = None
    consent_mode: bool = False
    all_connected_owners: bool = False

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        return frozenset({self.chat_id} if self.extra_chat_id is None else {self.chat_id, self.extra_chat_id})


def read_config(env: Mapping[str, str], now: float) -> ReplySaveConfig | None:
    if env.get(PREFIX + "CONSENT_MODE") == "1":
        if env.get(PREFIX + "ENABLED") != "1":
            return None
        if any(env.get(PREFIX + key, "") for key in ("CHAT_ID", "EXTRA_CHAT_ID", "EXPIRES_AT")):
            return None
        multi_raw = env.get(PREFIX + "ALL_CONNECTED_OWNERS", "")
        if multi_raw not in {"", "0", "1"}:
            return None
        all_connected = multi_raw == "1"
        if all_connected and env.get(PREFIX + "OWNER_ID", ""):
            return None
        try:
            names = ("BOT_ID",) if all_connected else ("BOT_ID", "OWNER_ID")
            raw_values = [env[PREFIX + key] for key in names]
            if not all(
                isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,15}", value)
                for value in raw_values
            ):
                return None
            parsed = list(map(int, raw_values))
            bot_id, owner_id = (parsed[0], 0) if all_connected else (parsed[0], parsed[1])
            if not 0 < bot_id < 2**52:
                return None
            if not all_connected and (not 0 < owner_id < 2**52 or bot_id == owner_id):
                return None
            return ReplySaveConfig(bot_id, owner_id, 0, 0, None, True, all_connected)
        except (KeyError, ValueError):
            return None
    if (PREFIX + "CONSENT_MODE" in env and env[PREFIX + "CONSENT_MODE"] not in {"", "0"}) or env.get(
        PREFIX + "ALL_CONNECTED_OWNERS", ""
    ) not in {"", "0"}:
        return None
    keys = ("ENABLED", "BOT_ID", "OWNER_ID", "CHAT_ID", "EXPIRES_AT")
    primary = shared.read_config({shared.PREFIX + key: env.get(PREFIX + key, "") for key in keys}, now)
    if primary is None:
        return None
    extra_id = None
    if PREFIX + "EXTRA_CHAT_ID" in env:
        raw = env[PREFIX + "EXTRA_CHAT_ID"]
        if not isinstance(raw, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", raw):
            return None
        extra_id = int(raw)
        if not 0 < extra_id < 2**52 or extra_id in {primary.bot_id, primary.owner_id, primary.chat_id}:
            return None
    return ReplySaveConfig(
        primary.bot_id, primary.owner_id, primary.chat_id, primary.expires_at, extra_id, False, False
    )


@dataclass(frozen=True)
class SaveCandidate:
    config: ReplySaveConfig
    source_key: tuple[str, int, int]
    file_id: str
    declared_size: int | None
    case: str
    archive_update: Update
    owner_id: int = 0


def select_candidate(bot_id: int, update: Update, config: ReplySaveConfig | None, now: float) -> SaveCandidate | None:
    """Pure selection: no state/IO and no mutation of the original Update."""
    message = update.business_message
    if (config is None or (not config.consent_mode and now >= config.expires_at)
            or bot_id != config.bot_id or message is None
            or message.chat.type != "private"
            or (config.consent_mode and (
                type(message.chat.id) is not int or not 1 < message.chat.id < 2**52
                or message.chat.id in {message.from_user.id if message.from_user else 0, config.bot_id}
            ))
            or (not config.consent_mode and message.chat.id not in config.allowed_chat_ids)
            or message.from_user is None or message.from_user.is_bot
            or (config.all_connected_owners and (
                type(message.from_user.id) is not int or not 1 < message.from_user.id < 2**52
                or message.from_user.id == config.bot_id
            ))
            or (not config.all_connected_owners and message.from_user.id != config.owner_id)
            or type(message.message_id) is not int or message.message_id <= 0 or message.date.timestamp() <= 0
            or message.is_from_offline or message.is_automatic_forward
            or message.sender_business_bot is not None or message.via_bot is not None
            or message.forward_origin is not None or message.external_reply is not None
            or not message.business_connection_id or len(message.business_connection_id) > 256):
        return None
    if any(value and value.lstrip().startswith(("!", "/")) for value in (message.text, message.caption)):
        return None
    reply = message.reply_to_message
    if (reply is None or reply.has_protected_content is not True or reply.chat.type != "private"
            or type(reply.chat.id) is not int or reply.chat.id != message.chat.id
            or type(reply.message_id) is not int or reply.message_id <= 0 or reply.date.timestamp() <= 0
            or reply.from_user is None or reply.from_user.is_bot or reply.from_user.id != message.chat.id
            or reply.sender_business_bot is not None or reply.via_bot is not None
            or reply.forward_origin is not None or reply.is_automatic_forward or reply.external_reply is not None
            or (reply.business_connection_id is not None and reply.business_connection_id != message.business_connection_id)
            or bool(reply.photo) == bool(reply.video)
            or any(getattr(reply, key, None) for key in (
                "document", "animation", "audio", "voice", "video_note", "sticker", "paid_media", "live_photo", "story"))):
        return None
    media = reply.photo[-1] if reply.photo else reply.video
    if media is None or not valid_file_id(media.file_id):
        return None
    minimal = Message.model_validate({"message_id": reply.message_id, "date": reply.date,
                                      "chat": {"id": reply.chat.id, "type": "private"}})
    archived_message = message.model_copy(deep=True, update={"reply_to_message": minimal})
    archive = update.model_copy(deep=True, update={"business_message": archived_message})
    return SaveCandidate(
        config, (message.business_connection_id, reply.chat.id, reply.message_id), media.file_id,
        media.file_size, "protected_photo" if reply.photo else "protected_video", archive,
        message.from_user.id,
    )


def emit(result: dict[str, Any]) -> None:
    try:
        logger.info("MEDIA_REPLY_SAVE %s", json.dumps(result, separators=(",", ":")))
    except Exception:
        pass


def valid_business_connection(value: Any, connection_id: str, owner_id: int) -> bool:
    user = value.get("user") if isinstance(value, dict) else None
    rights = value.get("rights") if isinstance(value, dict) else None
    return (
        isinstance(value, dict)
        and value.get("id") == connection_id
        and value.get("is_enabled") is True
        and isinstance(user, dict)
        and type(user.get("id")) is int
        and user["id"] == owner_id
        and user.get("is_bot") is False
        and isinstance(rights, dict)
        and rights.get("can_reply") is True
    )


async def read_media_limited(
    session: aiohttp.ClientSession, token: str, path: str, sizes: list[int],
    buffer: bytearray, case: str, result: dict[str, Any], max_bytes: int,
) -> tuple[str, str] | None:
    """Read one Telegram file while enforcing the current admin byte limit."""
    result["stage"] = "download"
    shared.reset_http(result)
    async with session.get(
        "https://api.telegram.org/file/bot" + token + "/" + path, allow_redirects=False
    ) as response:
        if type(response.status) is int and 100 <= response.status <= 599:
            result["http_status"] = response.status
        if (response.status != 200 or response.headers.get("Content-Range") is not None
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"):
            result["status"] = "download_denied"
            return None
        length = response.headers.get("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,12}", length):
                result["status"] = "download_denied"
                return None
            sizes.append(int(length))
        if any(size > max_bytes for size in sizes):
            result["status"] = "too_large"
            return None
        async for chunk in response.content.iter_chunked(shared.CHUNK_BYTES):
            count = len(buffer) + len(chunk)
            result["bytes_received"] = min(count, max_bytes)
            if len(chunk) > shared.CHUNK_BYTES or count > max_bytes:
                result["status"] = "too_large"
                return None
            buffer.extend(chunk)
        if not buffer:
            result["status"] = "empty"
            return None
        detected = timer.media_format(buffer, case)
        if detected is None:
            result["status"] = "format_mismatch"
            return None
        result["expected_format_match"] = True
        if not sizes or not any(size > 0 for size in sizes) or any(size != len(buffer) for size in sizes):
            result["status"] = "size_mismatch"
            return None
    result["download_complete"] = True
    return detected


async def transfer(
    bot: Bot,
    config: ReplySaveConfig,
    connection_id: str,
    file_id: str,
    declared_size: int | None,
    buffer: bytearray,
    result: dict[str, Any],
    owner_id: int | None = None,
    authorize: Callable[[], bool] | None = None,
    applied_policy: AppliedPolicy | None = None,
) -> None:
    token = bot.token
    if (not isinstance(token, str) or not re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{30,128}", token)
            or token.split(":", 1)[0] != str(config.bot_id)):
        return
    sizes = []
    if declared_size is not None:
        if type(declared_size) is not int or declared_size < 0:
            return
        sizes.append(declared_size)
    max_bytes = applied_policy.owner.limits.max_bytes if applied_policy is not None else MAX_BYTES
    if any(size > max_bytes for size in sizes):
        result["status"] = "too_large"
        return
    async with aiohttp.ClientSession(
        trust_env=False, auto_decompress=False, timeout=aiohttp.ClientTimeout(total=TOTAL_TIMEOUT),
        connector=aiohttp.TCPConnector(limit=1, ssl=True), cookie_jar=aiohttp.DummyCookieJar(),
        headers={"Accept-Encoding": "identity"},
    ) as session:
        expected_owner = config.owner_id if owner_id is None else owner_id
        if config.consent_mode:
            try:
                connection = await shared.api_json(
                    session, token, "getBusinessConnection",
                    {"business_connection_id": connection_id}, result,
                )
            except asyncio.CancelledError:
                shared.reset_http(result)
                result.update(status="connection_unverified", stage="precheck")
                raise
            except Exception:
                connection = None
            if not valid_business_connection(connection, connection_id, expected_owner):
                shared.reset_http(result)
                result.update(status="connection_unverified", stage="precheck")
                return
            if authorize is not None and not authorize():
                return
        delivery_config = replace(config, owner_id=expected_owner)
        result["stage"] = "get_file"
        info = await shared.api_json(session, token, "getFile", {"file_id": file_id}, result)
        if info is None:
            return
        result["get_file_ok"] = True
        path = info.get("file_path")
        if not isinstance(path, str) or not valid_path(path):
            result["status"] = "bad_path"
            return
        size = info.get("file_size")
        if size is not None:
            if type(size) is not int or size < 0:
                return
            sizes.append(size)
        if any(value > max_bytes for value in sizes):
            result["status"] = "too_large"
            return
        internal_case = "timer_photo" if result["case"] == "protected_photo" else "timer_video"
        detected = await read_media_limited(
            session, token, path, sizes, buffer, internal_case, result, max_bytes
        ) if applied_policy is not None else await timer.read_media(
            session, token, path, sizes, buffer, internal_case, result
        )
        if detected is None:
            return
        mime, _ = detected
        if applied_policy is not None:
            refreshed = await policy_service.effective(config.bot_id, expected_owner)
            if (refreshed is None or not refreshed.owner.enabled
                    or refreshed.owner != applied_policy.owner):
                shared.reset_http(result)
                result.update(status="policy_denied", stage="precheck")
                return
        filename = {"image/jpeg": "photo.jpg", "image/png": "photo.png", "video/mp4": "video.mp4"}[mime]
        form = aiohttp.FormData()
        for key, value in {"chat_id": str(expected_owner), "document": "attach://test_media",
                           "caption": CAPTION, "protect_content": "true", "disable_notification": "true",
                           "allow_paid_broadcast": "false", "disable_content_type_detection": "true"}.items():
            form.add_field(key, value)
        form.add_field("test_media", bytes(buffer), filename=filename,
                       content_type=mime)
        result["stage"] = "upload"
        shared.reset_http(result)
        sent = await shared.api_json(session, token, "sendDocument", form, result, upload=True,
                              expected=(delivery_config, len(buffer), mime))
        if sent is not None and shared.acknowledged(sent, delivery_config, len(buffer), mime):
            result.update(status="api_accepted", api_success=True)


@dataclass
class OwnerTransferState:
    starts: deque[float] = field(default_factory=deque)
    last_started: float | None = None
    last_attempt_at: float | None = None
    last_result: str | None = None
    successes: int = 0


@dataclass
class ReplySaveState:
    config: ReplySaveConfig | None
    seen: set[tuple[Any, ...]] = field(default_factory=set)
    busy: bool = False
    last_started: float | None = None
    deadline: float = field(default_factory=lambda: time.monotonic() + 3600)
    skips_logged: int = 0
    consent_starts: deque[float] = field(default_factory=deque)
    owners: dict[int, OwnerTransferState] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)

    def prepare(self, bot_id: int, update: Update, now: float) -> SaveCandidate | None:
        if self.config is not None and not self.config.consent_mode and time.monotonic() >= self.deadline:
            return None
        return select_candidate(bot_id, update, self.config, now)

    def skip(self, candidate: SaveCandidate, status: str) -> None:
        # Reserve up to four result slots for claims; repeated skips cannot hide them.
        reserved = CONSENT_ATTEMPTS_PER_HOUR if candidate.config.consent_mode else MAX_ATTEMPTS
        if self.skips_logged >= MAX_LOGS - reserved:
            return
        self.skips_logged += 1
        result = shared.outcome(candidate.case, True)
        result["status"] = status
        emit(result)

    async def after_commit(self, bot: Bot, candidate: SaveCandidate, now: float) -> None:
        config = self.config
        if config is None or config != candidate.config or bot.id != config.bot_id:
            return
        archived = candidate.archive_update.business_message
        if config.all_connected_owners and (
            archived is None or archived.from_user is None
            or archived.from_user.id != candidate.owner_id or archived.from_user.is_bot
            or archived.business_connection_id != candidate.source_key[0]
            or archived.chat.id != candidate.source_key[1]
        ):
            return
        current = time.monotonic()
        if not config.consent_mode and (now >= config.expires_at or current >= self.deadline):
            self.skip(candidate, "expired")
            return
        applied_policy = None
        if config.all_connected_owners:
            applied_policy = await policy_service.effective(config.bot_id, candidate.owner_id)
            if applied_policy is None or not applied_policy.owner.enabled:
                self.skip(candidate, "policy_denied")
                return
        dedup_key: tuple[Any, ...] = (
            (candidate.owner_id, *candidate.source_key)
            if config.all_connected_owners else candidate.source_key
        )
        if dedup_key in self.seen:
            self.skip(candidate, "duplicate")
            return
        if config.consent_mode:
            if len(self.seen) >= CONSENT_DEDUP_LIMIT:
                self.skip(candidate, "dedup_saturated")
                return
            if not config.all_connected_owners:
                while self.consent_starts and current - self.consent_starts[0] >= CONSENT_WINDOW_SECONDS:
                    self.consent_starts.popleft()
                if len(self.consent_starts) >= CONSENT_ATTEMPTS_PER_HOUR:
                    self.skip(candidate, "quota")
                    return
        elif len(self.seen) >= MAX_ATTEMPTS:
            self.skip(candidate, "quota")
            return
        # Claims are per Business source, not per webhook/reply update; never evict/retry.
        self.seen.add(dedup_key)
        result = shared.outcome(candidate.case, True)
        if self.busy:
            result["status"] = "busy"
            emit(result)
            return
        if (not config.all_connected_owners and self.last_started is not None
                and current - self.last_started < COOLDOWN_SECONDS):
            result["status"] = "cooldown"
            emit(result)
            return
        self.busy = True
        if not config.all_connected_owners:
            self.last_started = current
        if config.consent_mode and not config.all_connected_owners:
            self.consent_starts.append(current)
        buffer = bytearray()
        result["status"] = "unavailable"

        def authorize_owner() -> bool:
            verified_at = time.monotonic()
            owner = self.owners.get(candidate.owner_id)
            if owner is None:
                if len(self.owners) >= CONSENT_OWNER_LIMIT:
                    result["status"] = "owner_capacity"
                    return False
                owner = OwnerTransferState()
                self.owners[candidate.owner_id] = owner
            while owner.starts and verified_at - owner.starts[0] >= CONSENT_WINDOW_SECONDS:
                owner.starts.popleft()
            attempts_limit = (
                applied_policy.owner.limits.attempts_per_hour
                if applied_policy is not None else CONSENT_ATTEMPTS_PER_HOUR
            )
            cooldown = (
                applied_policy.owner.limits.cooldown_seconds
                if applied_policy is not None else COOLDOWN_SECONDS
            )
            if len(owner.starts) >= attempts_limit:
                result["status"] = "quota"
                return False
            if owner.last_started is not None and verified_at - owner.last_started < cooldown:
                result["status"] = "cooldown"
                return False
            owner.last_started = verified_at
            owner.last_attempt_at = time.time()
            owner.starts.append(verified_at)
            return True

        try:
            remaining = TOTAL_TIMEOUT if config.consent_mode else min(
                TOTAL_TIMEOUT, config.expires_at - now, self.deadline - current
            )
            async with asyncio.timeout(max(0, remaining)):
                transfer_args = (
                    bot, config, candidate.source_key[0], candidate.file_id,
                    candidate.declared_size, buffer, result,
                )
                if config.all_connected_owners:
                    await transfer(
                        *transfer_args, candidate.owner_id, authorize_owner,
                        applied_policy,
                    )
                else:
                    await transfer(*transfer_args)
        except asyncio.CancelledError:
            if result["status"] != "connection_unverified":
                shared.failure(result, timeout=False)
            emit(result)
            raise
        except TimeoutError:
            shared.failure(result, timeout=True)
        except Exception:
            shared.failure(result, timeout=False)
        finally:
            try:
                buffer.clear()
            except Exception:
                pass
            finally:
                self.busy = False
        emit(result)
        if config.all_connected_owners:
            owner = self.owners.get(candidate.owner_id)
            if owner is not None:
                owner.last_result = result["status"]
                if result["api_success"] is True:
                    owner.successes += 1

    def runtime_snapshot(self, now: float | None = None) -> dict[str, Any]:
        current = time.monotonic() if now is None else now
        owners = {}
        for owner_id, state in self.owners.items():
            while state.starts and current - state.starts[0] >= CONSENT_WINDOW_SECONDS:
                state.starts.popleft()
            owners[owner_id] = {
                "seen": True,
                "attempts_in_window": len(state.starts),
                "last_started": state.last_started,
                "last_attempt_at": state.last_attempt_at,
                "last_result": state.last_result,
                "successes": state.successes,
            }
        return {
            "busy": self.busy,
            "dedup_used": len(self.seen),
            "owners_loaded": len(self.owners),
            "owners": owners,
        }


_state: ReplySaveState | None = None


def get_state() -> ReplySaveState:
    global _state
    if _state is None:
        _state = ReplySaveState(read_config(os.environ, time.time()))
    return _state


def prepare_reply_save(bot_id: int, update: Update) -> SaveCandidate | None:
    return get_state().prepare(bot_id, update, time.time())


async def save_reply_after_commit(bot: Bot, candidate: SaveCandidate) -> None:
    await get_state().after_commit(bot, candidate, time.time())
