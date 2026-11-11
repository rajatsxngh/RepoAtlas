"""SBX-1: the hardened runner has no network, no host access, a read-only root, and limits."""

from __future__ import annotations

import time

import pytest

from repoatlas.sandbox.docker import docker
from repoatlas.sandbox.hardened import Limits, run_hardened
from repoatlas.sandbox.images import ensure_base_image
from tests.integration.sandbox.probe import PROBE_JS, parse_probe


@pytest.fixture(scope="module")
def image() -> str:
    return ensure_base_image("python").tag


def _run(image: str, script: str, **kwargs: object) -> tuple[int | None, str]:
    run = run_hardened(image, script, timeout_s=120, **kwargs)  # type: ignore[arg-type]
    return run.exit_code, run.log


def test_identity_capabilities_and_no_new_privileges(image: str) -> None:
    code, log = _run(image, "id -u; grep -E '^(CapEff|CapPrm|NoNewPrivs)' /proc/self/status")
    assert code == 0
    lines = log.splitlines()
    assert lines[0] == "10001"
    status = dict(line.split(":	") for line in lines[1:])
    assert status == {"CapEff": "0" * 16, "CapPrm": "0" * 16, "NoNewPrivs": "1"}


def test_no_network_interfaces_except_loopback(image: str) -> None:
    code, log = _run(image, "ls /sys/class/net")
    assert code == 0
    assert log.split() == ["lo"]


def test_no_network_and_no_host_access(image: str) -> None:
    probe = (
        'node -e "$PROBE" none 0 direct:1.1.1.1:443 direct:host.docker.internal:5433 '
        "direct:host.docker.internal:2375 direct:registry.npmjs.org:443 direct:172.17.0.1:5433"
    )
    run = run_hardened(image, probe, timeout_s=120, env={"PROBE": PROBE_JS})
    results = parse_probe(run.log.encode())
    assert len(results) == 5
    assert all(value != "OPEN" for value in results.values()), results


def test_no_docker_socket(image: str) -> None:
    code, _ = _run(image, "test ! -e /var/run/docker.sock && test ! -e /run/docker.sock")
    assert code == 0


def test_root_filesystem_is_read_only(image: str) -> None:
    script = (
        "for p in /src/x /etc/x /usr/x /opt/venv/x /home/sandbox/x; do "
        "touch $p 2>/dev/null && echo WROTE $p; done; "
        "touch /tmp/ok && echo TMP_OK; grep ' / ' /proc/mounts"
    )
    code, log = _run(image, script)
    assert code == 0
    assert "WROTE" not in log
    assert "TMP_OK" in log
    assert " ro," in log.splitlines()[-1]


def test_tmpfs_is_capped(image: str) -> None:
    script = (
        "dd if=/dev/zero of=/tmp/big bs=1M count=600 2>/dev/null; s=$?; "
        "size=$(du -m /tmp/big | cut -f1); rm -f /tmp/big; echo $s $size"
    )
    _, log = _run(image, script)
    status, size = log.split()
    assert status != "0"
    assert int(size) <= 512


def test_memory_limit_kills_the_process(image: str) -> None:
    script = "python -c 'x = bytearray(512 * 1024 * 1024); print(\"ALLOCATED\")'"
    code, log = _run(image, script, limits=Limits(memory="128m"))
    assert "ALLOCATED" not in log
    assert code not in (0, None)


def test_default_memory_and_pids_limits_are_applied(image: str) -> None:
    code, log = _run(image, "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.max")
    assert code == 0
    assert log.split() == [str(4 * 1024**3), "512"]


FORK_FLOOD = """python - <<'PY'
import subprocess
procs = []
try:
    for _ in range(64):
        procs.append(subprocess.Popen(["sleep", "30"]))
    print("STARTED", len(procs))
except OSError:
    print("BLOCKED", len(procs))
for proc in procs:
    proc.kill()
PY"""


def test_pids_limit_stops_a_fork_flood(image: str) -> None:
    _, log = _run(image, FORK_FLOOD, limits=Limits(pids=16))
    verdict, started = log.split()
    assert verdict == "BLOCKED"
    assert int(started) < 16


def test_timeout_kills_the_container(image: str) -> None:
    started = time.monotonic()
    run = run_hardened(image, "sleep 300", timeout_s=3, name_prefix="repoatlas-sbx-timeout")
    assert run.timed_out
    assert run.exit_code is None
    assert time.monotonic() - started < 60
    names = docker(["ps", "-a", "--filter", "name=repoatlas-sbx-timeout", "--format", "{{.Names}}"])
    assert names.text == ""


def test_out_dir_comes_back(image: str) -> None:
    run = run_hardened(image, "echo hello > /tmp/out/report; exit 3", timeout_s=60)
    assert run.exit_code == 3
    assert run.files["report"] == b"hello\n"
