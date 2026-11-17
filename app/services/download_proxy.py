"""Bounded loopback CONNECT proxy for trusted downloader subprocesses.

Every connection resolves DNS, rejects non-public answers and connects to the
checked IP (no second hostname lookup). Redirects require a fresh checked
tunnel. Only HTTPS/443 and public IPv4 are supported. No request URLs are logged.
This protects proxy traffic; callers MUST force all downloader traffic through
it and must never let ffmpeg open remote inputs or enable arbitrary plugins.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from contextlib import suppress

Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve_public_ipv4(host: str) -> list[str]:
    """Fail closed on mixed public/private answers, including numeric hosts."""
    if not host or len(host) > 253 or any(c in host for c in "\x00/\\@%[]:"):
        raise ValueError("Invalid download host")
    host = host.encode("idna").decode("ascii")
    answers = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(
            host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM
        ),
        timeout=5,
    )
    addresses = list(dict.fromkeys(str(answer[4][0]) for answer in answers))
    validate_public_addresses(addresses)
    return addresses


def validate_public_addresses(addresses: list[str]) -> None:
    if not addresses:
        raise ValueError("Download host has no public address")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if (
            address.version != 4
            or not address.is_global
            or address.is_multicast
            or address.is_reserved
        ):
            raise ValueError("Download destination is not public IPv4")


def parse_connect_header(header: bytes) -> str:
    """Never accepts HTTP forwarding, alternate ports, credentials or IPv6."""
    if len(header) > 8192:
        raise ValueError("Proxy header too large")
    line = header.split(b"\r\n", 1)[0].decode("ascii")
    method, authority, version = line.split(" ")
    if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1"):
        raise ValueError("HTTPS tunnel required")
    host, port = authority.rsplit(":", 1)
    if port != "443" or not host or any(c in host for c in "\x00/\\@%[]:"):
        raise ValueError("HTTPS port 443 required")
    return host


class DownloadProxy:
    """One-job ephemeral proxy; bounded sockets, bytes, headers and lifetime."""

    def __init__(
        self,
        *,
        resolver: Resolver = resolve_public_ipv4,
        max_bytes: int = 128 * 1024 * 1024,
        connection_limit: int = 8,
        timeout: float = 120,
    ) -> None:
        self.resolver = resolver
        self.max_bytes = max_bytes
        self.connection_limit = connection_limit
        self.timeout = timeout
        self.transferred = 0
        self.total_connections = 0
        self.tasks: set[asyncio.Task] = set()
        self.writers: set[asyncio.StreamWriter] = set()
        self.server: asyncio.Server | None = None
        self.url = ""
        self._closing = False

    async def __aenter__(self) -> DownloadProxy:
        self.server = await asyncio.start_server(
            self._accept, "127.0.0.1", 0, limit=8192, backlog=16
        )
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *_: object) -> None:
        self._closing = True
        if self.server:
            self.server.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        # A task cancelled before its first scheduling never enters _serve's
        # finally block. Track accepted sockets separately to close that race.
        writers = list(self.writers)
        for writer in writers:
            writer.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.difference_update(tasks)
        for writer in writers:
            with suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 1)
        self.writers.difference_update(writers)
        if self.server:
            await self.server.wait_closed()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if (
            self._closing
            or len(self.tasks) >= self.connection_limit
            or self.total_connections >= 128
            or self.transferred >= self.max_bytes
        ):
            writer.close()
            return
        self.total_connections += 1
        self.writers.add(writer)
        task = asyncio.create_task(self._serve(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while chunk := await reader.read(32 * 1024):
            self.transferred += len(chunk)
            if self.transferred > self.max_bytes:
                raise ValueError("Download transfer budget exceeded")
            writer.write(chunk)
            await writer.drain()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        relays: list[asyncio.Task] = []
        connected = False
        try:
            async with asyncio.timeout(self.timeout):
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                host = parse_connect_header(header)
                addresses = await asyncio.wait_for(self.resolver(host), 5)
                # Validate injected resolver outputs too, preventing future
                # custom resolvers from silently bypassing the security policy.
                validate_public_addresses(addresses)
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(addresses[0], 443, limit=32 * 1024), 8
                )
                upstream = remote_writer
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                connected = True
                relays = [
                    asyncio.create_task(self._relay(reader, remote_writer)),
                    asyncio.create_task(self._relay(remote_reader, writer)),
                ]
                done, _ = await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            if not connected:
                with suppress(Exception):
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    await writer.drain()
        finally:
            for task in relays:
                task.cancel()
            await asyncio.gather(*relays, return_exceptions=True)
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    with suppress(Exception):
                        await asyncio.wait_for(stream.wait_closed(), 1)
            self.writers.discard(writer)
