"""Hardened container runs (SBX-1).

Every container that runs repo code (tests, indexers) is started with ``hardened_run_args``;
nothing else in RepoAtlas starts such containers. Results come back as a tar of ``/tmp/out``
streamed on stdout: there are no bind mounts, so the container cannot see the host's files.

Docker Desktop on Windows runs containers in a shared WSL2 VM, so these flags are a defense
layer, not a guarantee. That is why only curated benchmark repos ever run here (SBX-11).
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from repoatlas.sandbox.docker import DockerError, docker_bin, remove_container

SANDBOX_USER = "10001:10001"
OUT_DIR = "/tmp/out"
LOG_FILE = ".log"
EXIT_FILE = ".exit"
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
LOG_TAIL_LINES = 60


@dataclass(frozen=True, slots=True)
class Limits:
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 512
    tmpfs_size: str = "512m"
    nofile: int = 4096


DEFAULT_LIMITS = Limits()


def hardened_run_args(
    name: str,
    image: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """The ``docker run`` arguments for running repo code. All security flags live here."""
    args = [
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        SANDBOX_USER,
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,size={limits.tmpfs_size},mode=1777",
        "--pids-limit",
        str(limits.pids),
        "--memory",
        limits.memory,
        "--memory-swap",
        limits.memory,  # equal to --memory: no swap on top of the limit
        "--cpus",
        limits.cpus,
        "--ulimit",
        f"nofile={limits.nofile}:{limits.nofile}",
        "--env",
        "HOME=/tmp",
    ]
    for key, value in sorted((env or {}).items()):
        args += ["--env", f"{key}={value}"]
    args.append(image)
    return args


@dataclass(slots=True)
class SandboxRun:
    exit_code: int | None  # the script's exit code; None when it was killed or never finished
    timed_out: bool
    duration_s: float
    files: dict[str, bytes] = field(default_factory=dict)  # everything under /tmp/out
    container_exit: int = 0  # docker run's own exit code (137 when killed)

    @property
    def log(self) -> str:
        return self.files.get(LOG_FILE, b"").decode("utf-8", "replace")

    def log_tail(self, lines: int = LOG_TAIL_LINES) -> str:
        return "\n".join(self.log.splitlines()[-lines:])


def wrap_script(script: str) -> str:
    """Run ``script`` with its output in /tmp/out/.log, then stream /tmp/out as a tar."""
    return (
        f"mkdir -p {OUT_DIR} && "
        f"( {script}\n) > {OUT_DIR}/{LOG_FILE} 2>&1; "
        f"echo $? > {OUT_DIR}/{EXIT_FILE}; "
        f"tar -C {OUT_DIR} -cf - ."
    )


def _read_tar(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if not data:
        return files
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is not None:
                files[member.name.removeprefix("./")] = handle.read()
    return files


def run_hardened(
    image: str,
    script: str,
    *,
    timeout_s: float,
    limits: Limits = DEFAULT_LIMITS,
    env: Mapping[str, str] | None = None,
    name_prefix: str = "repoatlas-sbx-run",
) -> SandboxRun:
    """Run ``script`` (POSIX sh) in a hardened container; kill it after ``timeout_s``."""
    name = f"{name_prefix}-{uuid.uuid4().hex[:12]}"
    args = [docker_bin(), *hardened_run_args(name, image, limits=limits, env=env)]
    # The script goes in on stdin, not the command line (which has a length limit on Windows).
    args.insert(2, "--interactive")
    args += ["sh", "-s"]
    started = time.monotonic()
    proc = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(wrap_script(script).encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        subprocess.run([docker_bin(), "kill", name], capture_output=True, timeout=60, check=False)
        stdout, stderr = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
        remove_container(name)
    duration = time.monotonic() - started
    if len(stdout) > MAX_OUTPUT_BYTES:
        raise DockerError(f"Sandbox output exceeded {MAX_OUTPUT_BYTES} bytes")
    try:
        files = {} if timed_out else _read_tar(stdout)
    except tarfile.TarError as exc:
        message = stderr.decode("utf-8", "replace").strip()[-2000:]
        raise DockerError(
            f"Sandbox run returned no results (exit {proc.returncode}): {message}"
        ) from exc
    exit_text = files.pop(EXIT_FILE, b"").decode().strip()
    return SandboxRun(
        exit_code=int(exit_text) if exit_text.lstrip("-").isdigit() else None,
        timed_out=timed_out,
        duration_s=round(duration, 2),
        files=files,
        container_exit=proc.returncode,
    )
