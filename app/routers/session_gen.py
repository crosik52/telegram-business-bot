"""Temporary one-time endpoint for generating a Telethon StringSession.

Visit /session-gen in the browser, follow the steps, copy the session string,
then save it as TELETHON_SESSION_STR in Replit Secrets and remove this router.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/session-gen", include_in_schema=False)

# ── In-memory state for the two-step auth flow ───────────────────────────────
_state: dict = {}  # keys: client, phone, phone_code_hash


def _page(title: str, body: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; background: #0f0f0f; color: #e8e8e8;
         display: flex; justify-content: center; padding: 48px 16px; }}
  .card {{ background: #1a1a1a; border: 1px solid #2e2e2e; border-radius: 12px;
           padding: 32px; width: 100%; max-width: 480px; }}
  h1 {{ font-size: 1.2rem; margin-bottom: 8px; color: #fff; }}
  p, label {{ font-size: 0.9rem; color: #aaa; line-height: 1.5; margin-bottom: 12px; }}
  input {{ width: 100%; padding: 10px 12px; background: #111; border: 1px solid #333;
           border-radius: 8px; color: #fff; font-size: 1rem; margin-bottom: 16px; }}
  button {{ width: 100%; padding: 10px; background: #2563eb; color: #fff;
            border: none; border-radius: 8px; font-size: 1rem; cursor: pointer; }}
  button:hover {{ background: #1d4ed8; }}
  .session {{ background: #111; border: 1px solid #333; border-radius: 8px;
              padding: 12px; font-family: monospace; font-size: 0.75rem;
              word-break: break-all; color: #6ee7b7; margin-top: 16px; }}
  .note {{ background: #1f2d1f; border: 1px solid #2d4a2d; border-radius: 8px;
           padding: 12px; font-size: 0.82rem; color: #86efac; margin-top: 16px; }}
  .err {{ background: #2d1f1f; border: 1px solid #5a2d2d; border-radius: 8px;
          padding: 12px; font-size: 0.85rem; color: #f87171; margin-top: 16px; }}
</style>
</head>
<body><div class="card">{body}</div></body>
</html>"""
    return HTMLResponse(html)


@router.get("", response_class=HTMLResponse)
async def step1_form() -> HTMLResponse:
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        return _page("Ошибка", """
<h1>⚠️ Не хватает переменных</h1>
<p style="margin-top:12px">
  Добавь <b>TELEGRAM_API_ID</b> и <b>TELEGRAM_API_HASH</b> в Secrets и перезапусти сервер.
</p>""")
    return _page("Генерация сессии — шаг 1", """
<h1>📱 Шаг 1 — Введи номер телефона</h1>
<p style="margin-top:12px">Telegram отправит код на этот номер (аккаунт с Business).</p>
<form method="post" action="/session-gen/send-code">
  <label>Номер телефона (с кодом страны)</label>
  <input name="phone" type="tel" placeholder="+79991234567" required autofocus>
  <button type="submit">Отправить код →</button>
</form>""")


@router.post("/send-code", response_class=HTMLResponse)
async def step2_send_code(phone: str = Form(...)) -> HTMLResponse:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        _state.clear()
        _state.update({"client": client, "phone": phone,
                        "phone_code_hash": result.phone_code_hash})
        return _page("Генерация сессии — шаг 2", f"""
<h1>✉️ Шаг 2 — Введи код из Telegram</h1>
<p style="margin-top:12px">Код отправлен на <b>{phone}</b>.<br>
Введи его ниже (без пробелов).</p>
<form method="post" action="/session-gen/sign-in">
  <label>Код подтверждения</label>
  <input name="code" type="text" placeholder="12345" required autofocus
         inputmode="numeric" maxlength="10">
  <label style="margin-top:4px">Пароль 2FA (если включён, иначе оставь пустым)</label>
  <input name="password" type="password" placeholder="Не обязательно">
  <button type="submit">Войти и получить сессию →</button>
</form>""")
    except Exception as exc:
        return _page("Ошибка", f"""
<h1>❌ Не удалось отправить код</h1>
<div class="err">{exc}</div>
<p style="margin-top:16px"><a href="/session-gen" style="color:#60a5fa">← Попробовать снова</a></p>""")


@router.post("/sign-in", response_class=HTMLResponse)
async def step3_sign_in(
    code: str = Form(...),
    password: Optional[str] = Form(default=None),
) -> HTMLResponse:
    client = _state.get("client")
    phone = _state.get("phone")
    phone_code_hash = _state.get("phone_code_hash")
    if not client or not phone or not phone_code_hash:
        return _page("Ошибка", """
<h1>⚠️ Сессия истекла</h1>
<p style="margin-top:12px">Начни заново.</p>
<p><a href="/session-gen" style="color:#60a5fa">← Начать сначала</a></p>""")
    try:
        try:
            await client.sign_in(phone=phone, code=code,
                                  phone_code_hash=phone_code_hash)
        except Exception as e:
            # 2FA required
            if "two" in str(e).lower() or "password" in str(e).lower() or "SessionPasswordNeeded" in type(e).__name__:
                if not password:
                    return _page("Нужен пароль 2FA", """
<h1>🔐 Введи пароль 2FA</h1>
<form method="post" action="/session-gen/sign-in">
  <input name="code" type="hidden" value=\"""" + code + """\">
  <label>Пароль двухфакторной аутентификации</label>
  <input name="password" type="password" placeholder="Пароль 2FA" required autofocus>
  <button type="submit">Войти →</button>
</form>""")
                await client.sign_in(password=password)
            else:
                raise

        session_str = client.session.save()
        await client.disconnect()
        _state.clear()

        return _page("✅ Готово!", f"""
<h1>✅ Строка сессии получена</h1>
<p style="margin-top:12px">Скопируй строку ниже и сохрани как секрет
<b>TELETHON_SESSION_STR</b> в Replit Secrets:</p>
<div class="session">{session_str}</div>
<div class="note">
  <b>Что дальше:</b><br>
  1. Скопируй строку выше<br>
  2. В Replit → Secrets → добавь <code>TELETHON_SESSION_STR</code><br>
  3. Перезапусти сервер<br>
  4. Бот начнёт перехватывать одноразки автоматически
</div>""")
    except Exception as exc:
        return _page("Ошибка входа", f"""
<h1>❌ Не удалось войти</h1>
<div class="err">{exc}</div>
<p style="margin-top:16px"><a href="/session-gen" style="color:#60a5fa">← Попробовать снова</a></p>""")
