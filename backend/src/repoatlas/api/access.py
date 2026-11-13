"""The private access gate (DEP-6): one shared token for the whole deployment.

When ``REPOATLAS_ACCESS_TOKEN`` is set, every request under the API prefix must carry it, as
``Authorization: Bearer <token>`` or as the ``repoatlas_access`` cookie, whose value is
``cookie_value(token)`` (a digest of the token, so the token itself is typed once and never
kept in the browser). The web app sets that cookie after its one-time token entry and forwards
API calls from its own origin. Anything else gets 401 ``unauthorized``.

Open without the token: the liveness probe (``/health``, which platforms call without
credentials and which says only that the process is up) and CORS preflights (browsers send
them without credentials).

Comparisons take the same time whatever the input (``hmac.compare_digest``). The token is never
logged: this module logs nothing, and requests are logged without their headers. The token is
not accepted in the query string, where proxies and browsers would record it.
"""

from __future__ import annotations

import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie

from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from repoatlas.api.errors import ErrorCode, error_response

ACCESS_COOKIE = "repoatlas_access"
_COOKIE_CONTEXT = b"repoatlas-access:"
_BEARER = "bearer "


def cookie_value(token: str) -> str:
    """What the access cookie holds for ``token``: a sha256 digest, in hex."""
    return hashlib.sha256(_COOKIE_CONTEXT + token.encode("utf-8")).hexdigest()


def _same(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


class AccessGateMiddleware:
    """Answer 401 unless the request carries the deployment's access token."""

    def __init__(self, app: ASGIApp, *, token: SecretStr, prefix: str) -> None:
        if not token.get_secret_value():
            raise ValueError("The access token can't be empty.")
        self.app = app
        self.prefix = prefix
        self._token = token
        self._cookie = cookie_value(token.get_secret_value())
        self._open = {f"{prefix}/health"}

    def allowed(self, headers: Headers) -> bool:
        authorization = headers.get("authorization", "")
        if authorization[: len(_BEARER)].lower() == _BEARER:
            given = authorization[len(_BEARER) :].strip()
            return _same(given, self._token.get_secret_value())
        raw = headers.get("cookie")
        if raw:
            try:
                morsel = SimpleCookie(raw).get(ACCESS_COOKIE)
            except CookieError:
                return False
            if morsel is not None:
                return _same(morsel.value, self._cookie)
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        gated = path.startswith(self.prefix + "/") or path == self.prefix
        if (
            not gated
            or scope.get("method") == "OPTIONS"
            or path.rstrip("/") in self._open
            or self.allowed(Headers(scope=scope))
        ):
            await self.app(scope, receive, send)
            return
        response = error_response(
            401,
            ErrorCode.UNAUTHORIZED,
            "This deployment is private: an access token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)
