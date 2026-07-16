"""One-time interactive script to generate a Telethon StringSession.

Run locally (NOT on Railway):
    python scripts/generate_telethon_session.py

It will ask for your phone number, send a Telegram code, and print the
session string.  Copy that string and set it as TELETHON_SESSION_STR in
your Railway / .env environment variables.

You also need:
    TELEGRAM_API_ID   — integer from https://my.telegram.org
    TELEGRAM_API_HASH — string  from https://my.telegram.org
"""

import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id_raw = os.environ.get("TELEGRAM_API_ID") or input("Enter API ID: ").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or input("Enter API Hash: ").strip()
    api_id = int(api_id_raw)

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start()  # prompts for phone + code interactively
        session_str = client.session.save()

    print("\n" + "=" * 60)
    print("SESSION STRING (save this as TELETHON_SESSION_STR):")
    print("=" * 60)
    print(session_str)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
