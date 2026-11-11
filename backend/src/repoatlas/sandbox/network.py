"""The internal build network and its allowlist proxy (SBX-2).

Image builds that install dependencies run on ``INTERNAL_NETWORK``, created with ``--internal``:
it has no gateway, so containers on it cannot reach the internet, the Docker host, or the dev
Postgres. The proxy container is the only member that is also on the default bridge; it forwards
HTTPS (``CONNECT``) to the package registries in ``sandbox/proxy/squid.conf`` and denies the rest.
"""

from __future__ import annotations

from repoatlas.sandbox.docker import DockerError, docker, image_id
from repoatlas.sandbox.paths import sandbox_dir

INTERNAL_NETWORK = "repoatlas-sbx-internal"
PROXY_CONTAINER = "repoatlas-sbx-proxy"
PROXY_IMAGE = "repoatlas-sbx/proxy:1"
PROXY_PORT = 3128
PROXY_URL = f"http://{PROXY_CONTAINER}:{PROXY_PORT}"
ALLOWED_HOSTS = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org")


def proxy_env() -> dict[str, str]:
    """Environment that sends pip, uv, npm, and corepack through the proxy."""
    env = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env[name] = PROXY_URL
    env["npm_config_proxy"] = PROXY_URL
    env["npm_config_https_proxy"] = PROXY_URL
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    return env


def ensure_network() -> None:
    result = docker(
        ["network", "inspect", "--format", "{{.Internal}}", INTERNAL_NETWORK], check=False
    )
    if result.returncode == 0:
        if result.text != "true":
            raise DockerError(f"Network {INTERNAL_NETWORK} exists but is not internal; remove it.")
        return
    docker(["network", "create", "--internal", "--driver", "bridge", INTERNAL_NETWORK])


def _running_image(name: str) -> str | None:
    result = docker(
        ["container", "inspect", "--format", "{{.State.Running}} {{.Image}}", name], check=False
    )
    if result.returncode != 0:
        return None
    running, _, image = result.text.partition(" ")
    return image if running == "true" else None


def ensure_proxy() -> str:
    """Build the proxy image, create the network, and start the proxy; returns the image ID."""
    docker(["build", "--network", "none", "-q", "-t", PROXY_IMAGE, str(sandbox_dir() / "proxy")])
    wanted = image_id(PROXY_IMAGE)
    assert wanted is not None
    ensure_network()
    if _running_image(PROXY_CONTAINER) == wanted:
        return wanted
    docker(["rm", "-f", PROXY_CONTAINER], check=False)
    docker(
        [
            "run",
            "-d",
            "--name",
            PROXY_CONTAINER,
            "--network",
            "bridge",
            "--cap-drop",
            "ALL",
            *("--cap-add", "SETUID", "--cap-add", "SETGID", "--cap-add", "CHOWN"),
            *("--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER"),
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "512m",
            "--pids-limit",
            "256",
            PROXY_IMAGE,
        ]
    )
    docker(["network", "connect", INTERNAL_NETWORK, PROXY_CONTAINER])
    return wanted


def proxy_down() -> None:
    docker(["rm", "-f", PROXY_CONTAINER], check=False)
    docker(["network", "rm", INTERNAL_NETWORK], check=False)
