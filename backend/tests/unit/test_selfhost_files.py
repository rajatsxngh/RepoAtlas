"""The self-host stack keeps its promises (DEP-2, DEP-5, DEP-6): migrations before the
services, nothing but the web app published, a required access token, no secrets in the
examples, and production caps within the development defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from repoatlas.config import Settings

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"


def compose() -> dict[str, Any]:
    loaded = yaml.safe_load((INFRA / "compose.selfhost.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_selfhost_stack_has_every_service_and_publishes_only_the_web_app() -> None:
    services = compose()["services"]
    assert set(services) == {"db", "migrate", "api", "worker", "web"}
    published = {name for name, service in services.items() if service.get("ports")}
    assert published == {"web"}
    (port,) = services["web"]["ports"]
    assert port.startswith("${REPOATLAS_WEB_BIND:-127.0.0.1}:")  # this machine only by default
    assert "@sha256:" in services["db"]["image"]


def test_migrations_run_before_the_api_and_the_worker_start() -> None:
    services = compose()["services"]
    assert services["migrate"]["command"] == ["repoatlas", "db", "upgrade"]
    assert services["migrate"]["depends_on"] == {"db": {"condition": "service_healthy"}}
    for name in ("api", "worker"):
        assert services[name]["depends_on"] == {
            "migrate": {"condition": "service_completed_successfully"}
        }
    assert services["web"]["depends_on"] == {"api": {"condition": "service_healthy"}}


def test_the_stack_is_gated_and_hardened() -> None:
    services = compose()["services"]
    for name in ("api", "web"):
        assert services[name]["environment"]["REPOATLAS_ACCESS_TOKEN"].startswith(
            "${REPOATLAS_ACCESS_TOKEN:?"
        )  # compose refuses to start without a token
    assert services["api"]["environment"]["REPOATLAS_TRUST_PROXY_HEADERS"] == "true"
    assert services["web"]["environment"]["REPOATLAS_API_ORIGIN"] == "http://api:8000"
    for name in ("migrate", "api", "worker", "web"):
        assert services[name]["cap_drop"] == ["ALL"]
        assert services[name]["security_opt"] == ["no-new-privileges:true"]


def env_file(name: str) -> dict[str, str]:
    values = {}
    for line in (INFRA / name).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


def test_the_example_env_files_hold_no_secrets() -> None:
    example = env_file("selfhost.env.example")
    for key in ("REPOATLAS_ACCESS_TOKEN", "POSTGRES_PASSWORD", "REPOATLAS_ANTHROPIC_API_KEY"):
        assert example[key] == ""
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "infra/*.env" in ignored


def test_production_caps_are_real_settings_no_larger_than_the_development_defaults() -> None:
    defaults = Settings(_env_file=None)
    production = env_file("production.env.example")
    assert {
        "REPOATLAS_DAILY_SPEND_CAP_USD",
        "REPOATLAS_ASKS_PER_DAY",
        "REPOATLAS_BUILDS_PER_DAY",
        "REPOATLAS_JOB_MAX_RUNNING",
        "REPOATLAS_MAX_REPO_SIZE_KB",
        "REPOATLAS_MAX_SOURCE_FILES",
        "REPOATLAS_MAX_FILE_BYTES",
    } <= set(production)
    for key, value in production.items():
        field = key.removeprefix("REPOATLAS_").lower()
        assert field in Settings.model_fields, f"{key} is not a setting"
        default = getattr(defaults, field)
        assert 0 < float(value) <= float(default), f"{key}={value} exceeds the default {default}"
    # The compose file passes each limit it names through to the backend.
    passed = compose()["x-backend-env"]
    for key in (
        "REPOATLAS_DAILY_SPEND_CAP_USD",
        "REPOATLAS_ASKS_PER_DAY",
        "REPOATLAS_MAX_REPO_SIZE_KB",
    ):
        assert key in passed
