"""Bearer-token auth middleware for the streamable-http MCP transport.

A deliberately simple, static-secret check - not OAuth. The MCP SDK ships
a full OAuth 2.1 `auth`/`token_verifier` system (issuer URLs, protected-
resource metadata, WWW-Authenticate discovery, etc.), which is real
infrastructure for multi-client/multi-tenant servers. That's unnecessary
complexity here: this server has exactly one intended caller (a Claude Pro
custom connector) holding one shared secret, so a plain
"Authorization: Bearer <token>" check against an env var is the right
amount of protection.

Only ever used when running the streamable-http transport (see the
MCP_TRANSPORT branch in mcp_server.py) - stdio/MCP Inspector never goes
through this code path at all.
"""

import hmac

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

# Paths that stay reachable without a token - just the health check, so a
# hosting platform's liveness probe doesn't need credentials.
PUBLIC_PATHS = {"/health"}


class BearerTokenMiddleware:
    """Raw ASGI middleware.

    Rejects any HTTP request outside PUBLIC_PATHS with 401 unless it
    carries "Authorization: Bearer <token>" matching the configured token.
    Non-HTTP ASGI events (e.g. the app's own lifespan startup) pass
    through untouched.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        provided = authorization[len("Bearer "):] if authorization.startswith("Bearer ") else ""

        # Constant-time comparison - a plain "==" would let response timing
        # leak how many leading characters of a guessed token were correct.
        if not provided or not hmac.compare_digest(provided, self.token):
            # Body intentionally says nothing about *why* - never echoes
            # back the token that was sent or the one that was expected.
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
