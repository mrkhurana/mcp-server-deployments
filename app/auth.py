import hmac
import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("aiops-mcp")

# Paths reachable without a token: the ALB/container health check.
PUBLIC_PATHS = {"/health"}


class BearerTokenMiddleware:
    """Require `Authorization: Bearer <token>` on every HTTP request except PUBLIC_PATHS.

    Pure ASGI (not BaseHTTPMiddleware) so MCP's streaming responses pass through untouched.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        provided = dict(scope.get("headers") or []).get(b"authorization", b"")
        if hmac.compare_digest(provided, self.expected):
            await self.app(scope, receive, send)
            return

        client = (scope.get("client") or ("?", 0))[0]
        logger.warning("rejected unauthenticated request from %s to %s", client, scope["path"])
        body = json.dumps({"error": "unauthorized"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
