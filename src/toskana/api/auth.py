"""Optional bearer-token authentication for the API.

When ``api_token`` is configured (``TOSKANA_API_TOKEN``) every ``/api``
route and ``/ws/live`` require the token; ``GET /api/system/health`` stays
open so monitoring probes keep working. The token is accepted as an
``Authorization: Bearer <token>`` header or — for clients that cannot set
headers (``<img>`` MJPEG/snapshot tags, ``<a download>`` CSV links, the
WebSocket) — as a ``?token=`` query parameter.

When ``api_token`` is None (the default, LAN mode) the middleware is not
installed at all and behavior is unchanged.
"""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: (method, path) pairs that never require the token.
_EXEMPT = {("GET", "/api/system/health")}

#: WebSocket close code for a failed handshake (policy violation).
_WS_POLICY_VIOLATION = 1008


class TokenAuthMiddleware:
    """Pure-ASGI middleware: 401 (or WS close) unless the token matches."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("api_token must be non-empty")
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._protected(scope) or self._authorized(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # Closing before accept rejects the handshake (HTTP 403).
            await send({"type": "websocket.close", "code": _WS_POLICY_VIOLATION})
            return
        response = JSONResponse(
            {"detail": "missing or invalid API token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    @staticmethod
    def _protected(scope: Scope) -> bool:
        if scope["type"] not in ("http", "websocket"):
            return False
        path: str = scope["path"]
        if scope["type"] == "http":
            if (scope["method"], path) in _EXEMPT:
                return False
            return path == "/api" or path.startswith("/api/")
        return path == "/ws" or path.startswith("/ws/")

    def _authorized(self, scope: Scope) -> bool:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer" and self._matches(credential.strip()):
            return True
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        return any(self._matches(candidate) for candidate in query.get("token", []))

    def _matches(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate.encode(), self._token.encode())
