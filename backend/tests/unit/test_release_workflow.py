"""The release workflow (DEP-4): the stack check needs no secrets, publishing and deploying are
switched off unless DEPLOY_ENABLED is set, and migrations run before the deploy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def triggers(loaded: dict[str, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the key "on" as the boolean true.
    found = loaded.get("on", loaded.get(True))
    assert isinstance(found, dict)
    return found


def step_names(job: dict[str, Any]) -> list[str]:
    return [step.get("name", step.get("uses", "")) for step in job["steps"]]


def test_it_runs_on_tags_manual_runs_and_changes_to_the_deployment_files() -> None:
    on = triggers(workflow())
    assert on["push"]["tags"] == ["v*"]
    assert "infra/**" in on["push"]["paths"]
    assert "scripts/selfhost_check.py" in on["push"]["paths"]
    assert "version" in on["workflow_dispatch"]["inputs"]  # deploy an existing version: rollback
    assert workflow()["permissions"] == {"contents": "read"}


def test_the_stack_job_builds_checks_and_uses_no_secrets() -> None:
    job = workflow()["jobs"]["stack"]
    text = yaml.safe_dump(job)
    assert "secrets." not in text
    assert "REPOATLAS_LLM_TRANSPORT=offline" in text  # no model, no cost
    assert "ANTHROPIC" not in text
    names = step_names(job)
    order = [
        "Build the API, worker, and web images",
        "The images run without root",
        "Start the stack (migrations run first)",
        "Migrations ran before the services started",
        "End-to-end check (gate, build, question, impact analysis)",
    ]
    positions = [names.index(name) for name in order]
    assert positions == sorted(positions)
    check = job["steps"][positions[-1]]["run"]
    assert "scripts/selfhost_check.py" in check and "--expect-offline" in check


def test_nothing_is_published_or_deployed_unless_deployment_is_switched_on() -> None:
    jobs = workflow()["jobs"]
    assert set(jobs) == {"stack", "publish", "deploy"}
    for name in ("publish", "deploy"):
        assert "vars.DEPLOY_ENABLED == 'true'" in jobs[name]["if"], name
    # A branch push never publishes: only a tag or a manual run does.
    assert "github.ref_type == 'tag'" in jobs["publish"]["if"]
    assert "workflow_dispatch" in jobs["publish"]["if"]
    assert jobs["publish"]["needs"] == "stack"
    assert jobs["publish"]["permissions"]["packages"] == "write"
    assert jobs["deploy"]["environment"] == "production"


def test_the_deploy_job_migrates_first_and_smoke_checks_last() -> None:
    job = workflow()["jobs"]["deploy"]
    names = step_names(job)
    migrate = next(index for index, name in enumerate(names) if name.startswith("Migrate"))
    deploys = [index for index, name in enumerate(names) if name.startswith("Deploy")]
    smoke = next(index for index, name in enumerate(names) if name.startswith("Smoke check"))
    assert deploys and migrate < min(deploys) and max(deploys) < smoke == len(names) - 1
    assert "repoatlas db upgrade" in job["steps"][migrate]["run"]
    assert "--only gate" in job["steps"][smoke]["run"]
    # Secrets reach commands through the environment, never through the command line.
    for step in job["steps"]:
        assert "secrets." not in step.get("run", ""), step.get("name")


def test_images_built_for_publishing_are_the_three_dockerfiles() -> None:
    publish = workflow()["jobs"]["publish"]
    assert publish["strategy"]["matrix"]["image"] == ["api", "worker", "web"]
    for image in publish["strategy"]["matrix"]["image"]:
        assert (ROOT / "infra" / "docker" / f"{image}.Dockerfile").is_file()
