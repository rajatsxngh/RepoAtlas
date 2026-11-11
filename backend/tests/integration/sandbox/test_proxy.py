"""SBX-2: on the internal network, only the allowlisted registries are reachable, via the proxy."""

from __future__ import annotations

import pytest

from repoatlas.sandbox.docker import docker
from repoatlas.sandbox.network import (
    INTERNAL_NETWORK,
    PROXY_CONTAINER,
    PROXY_PORT,
    ensure_proxy,
)
from tests.integration.sandbox.probe import parse_probe, probe_args

# Any image with Node works for the probe; the official one is pinned like the base images.
NODE_IMAGE = (
    "docker.io/library/node:22-bookworm-slim"
    "@sha256:18052396bf1d0c6e0a8c58181b5c1457da19409efbd5032a3f4d86974e74d13c"
)


@pytest.fixture(scope="module")
def probe_results() -> dict[str, str]:
    ensure_proxy()
    targets = [
        "proxy:registry.npmjs.org",
        "proxy:pypi.org",
        "proxy:files.pythonhosted.org",
        "proxy:example.com",
        "proxy:github.com",
        "proxy:host.docker.internal",
        "direct:registry.npmjs.org:443",
        "direct:1.1.1.1:443",
        "direct:host.docker.internal:5433",
        "direct:host.docker.internal:2375",
    ]
    result = docker(
        [
            "run",
            "--rm",
            "--network",
            INTERNAL_NETWORK,
            NODE_IMAGE,
            *probe_args(PROXY_CONTAINER, PROXY_PORT, targets),
        ],
        timeout=300,
    )
    return parse_probe(result.stdout)


@pytest.mark.parametrize("host", ["registry.npmjs.org", "pypi.org", "files.pythonhosted.org"])
def test_allowlisted_registries_are_reachable(probe_results: dict[str, str], host: str) -> None:
    assert probe_results[f"proxy:{host}"].endswith("200 Connection established")


@pytest.mark.parametrize("host", ["example.com", "github.com", "host.docker.internal"])
def test_other_hosts_are_denied_by_the_proxy(probe_results: dict[str, str], host: str) -> None:
    assert " 403 " in probe_results[f"proxy:{host}"]


@pytest.mark.parametrize(
    "target",
    [
        "direct:registry.npmjs.org:443",
        "direct:1.1.1.1:443",
        "direct:host.docker.internal:5433",  # the dev Postgres
        "direct:host.docker.internal:2375",  # the Docker daemon's TCP port
    ],
)
def test_nothing_is_reachable_without_the_proxy(probe_results: dict[str, str], target: str) -> None:
    assert probe_results[target] != "OPEN"


def test_network_is_internal() -> None:
    ensure_proxy()
    result = docker(["network", "inspect", "--format", "{{.Internal}}", INTERNAL_NETWORK])
    assert result.text == "true"
