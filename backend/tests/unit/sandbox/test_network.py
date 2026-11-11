from __future__ import annotations

import re

from repoatlas.sandbox.network import ALLOWED_HOSTS, PROXY_URL, proxy_env
from repoatlas.sandbox.paths import sandbox_dir


def test_squid_allowlist_matches_the_code() -> None:
    conf = (sandbox_dir() / "proxy" / "squid.conf").read_text()
    match = re.search(r"^acl registries dstdomain (.+)$", conf, re.MULTILINE)
    assert match is not None
    assert tuple(match.group(1).split()) == ALLOWED_HOSTS
    # Only CONNECT to port 443 of a registry is allowed, and everything else is denied last.
    assert "http_access allow CONNECT registries\nhttp_access deny all" in conf
    assert "http_access deny private_dst" in conf


def test_proxy_base_image_is_pinned_by_digest() -> None:
    dockerfile = (sandbox_dir() / "proxy" / "Dockerfile").read_text()
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)


def test_proxy_env_covers_pip_uv_and_npm() -> None:
    env = proxy_env()
    for name in ("HTTPS_PROXY", "https_proxy", "npm_config_https_proxy"):
        assert env[name] == PROXY_URL
