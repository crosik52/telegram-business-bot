"""Reverse proxy: forwards /__mockup/* requests to the Vite mockup dev server
running on port 3000. Only active in development / canvas exploration sessions.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()

_VITE_BASE = "http://127.0.0.1:3000"
_CLIENT = httpx.AsyncClient(base_url=_VITE_BASE, timeout=30.0, follow_redirects=True)

# Headers that must not be forwarded to avoid encoding / connection conflicts.
_HOP_BY_HOP = {
    "host",
    "connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
    "keep-alive",
}


@router.api_route(
    "/__mockup/{path:path}",
    methods=["GET", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def mockup_proxy(path: str, request: Request) -> Response:
    """Stream the request to the local Vite dev server and return its response."""
    target_url = f"/__mockup/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
    }

    try:
        upstream = await _CLIENT.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=await request.body(),
        )
    except httpx.ConnectError:
        return Response(
            content=b"Mockup sandbox is not running (start the Component Preview Server workflow).",
            status_code=503,
            media_type="text/plain",
        )

    response_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
