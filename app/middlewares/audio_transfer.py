"""Outermost audio admission, including buffered middleware and client sends."""

import asyncio

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class AudioTransferMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/audio/"):
            await self.app(scope, receive, send)
            return
        if self.active >= 2:
            await PlainTextResponse("Audio service busy", status_code=429,
                                    headers={"Retry-After": "5"})(scope, receive, send)
            return
        self.active += 1

        async def bounded_send(message):
            async with asyncio.timeout(30):
                await send(message)

        try:
            # Hold outside BaseHTTPMiddleware: inner responses may already be
            # buffered there while a slow client still retains the whole body.
            await self.app(scope, receive, bounded_send)
        finally:
            self.active -= 1
