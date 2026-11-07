"""Symbol keys in URL paths: kept in one segment by the middleware, decoded once by the route."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest

from repoatlas.api.deps import decode_key
from repoatlas.api.errors import ApiError
from repoatlas.api.middleware import EncodedKeyMiddleware

pytestmark = pytest.mark.anyio
KEY = "ts:web/cart.test.ts:cart%3A 100%25 > ok > adds / removes:test"


async def routed_path(raw_path: bytes | None, path: str) -> str:
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], _receive: Any, _send: Any) -> None:
        seen.update(scope)

    scope: dict[str, Any] = {"type": "http", "path": path}
    if raw_path is not None:
        scope["raw_path"] = raw_path
    await EncodedKeyMiddleware(app, prefix="/api/v1")(scope, None, None)  # type: ignore[arg-type]
    assert scope["path"] == path  # the caller's scope is never changed
    return str(seen["path"])


async def test_node_paths_keep_the_raw_encoded_key() -> None:
    encoded = quote(KEY, safe="")
    raw = f"/api/v1/snapshots/3/nodes/{encoded}".encode("ascii")
    decoded = f"/api/v1/snapshots/3/nodes/{KEY.replace('%3A', ':').replace('%25', '%')}"
    assert await routed_path(raw, decoded) == raw.decode()
    neighbors = raw + b"/neighbors"
    assert await routed_path(neighbors, decoded + "/neighbors") == neighbors.decode()
    assert decode_key(encoded) == KEY  # once: %253A becomes %3A, not ':'


async def test_other_paths_and_odd_requests_are_left_alone() -> None:
    assert await routed_path(b"/api/v1/snapshots/3/code", "/api/v1/snapshots/3/code") == (
        "/api/v1/snapshots/3/code"
    )
    assert await routed_path(None, "/api/v1/snapshots/3/nodes/a") == "/api/v1/snapshots/3/nodes/a"
    raw = "/api/v1/snapshots/3/nodes/café".encode()  # raw non-ASCII bytes
    assert await routed_path(raw, "/api/v1/snapshots/3/nodes/x") == "/api/v1/snapshots/3/nodes/x"
    other = b"/api/v1/snapshots/3/nodes/a/b/c"  # a key with raw slashes matches no route
    assert await routed_path(other, other.decode()) == other.decode()


@pytest.mark.parametrize("raw", ["%FF", "a%00b", "", "k" * 5_000])
def test_bad_keys_are_invalid_requests(raw: str) -> None:
    with pytest.raises(ApiError) as caught:
        decode_key(raw)
    assert (caught.value.status_code, caught.value.code.value) == (422, "invalid_request")
