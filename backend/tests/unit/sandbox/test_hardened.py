from __future__ import annotations

from repoatlas.sandbox.hardened import Limits, hardened_run_args, wrap_script


def test_hardened_flags_match_decision_0012() -> None:
    args = hardened_run_args("c1", "img:1", env={"B": "2", "A": "1"})
    joined = " ".join(args)
    for flag in (
        "--network none",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--user 10001:10001",
        "--read-only",
        "--tmpfs /tmp:rw,size=512m,mode=1777",
        "--pids-limit 512",
        "--memory 4g",
        "--memory-swap 4g",
        "--cpus 2",
        "--ulimit nofile=4096:4096",
    ):
        assert flag in joined, flag
    assert args[-1] == "img:1"
    assert args.index("--env") < args.index("img:1")
    assert "A=1" in args and args.index("A=1") < args.index("B=2")


def test_no_mounts_no_socket_no_privileges() -> None:
    args = hardened_run_args("c1", "img:1")
    for forbidden in ("-v", "--volume", "--mount", "--privileged", "--cap-add", "--device", "-p"):
        assert forbidden not in args
    assert not any("docker.sock" in arg for arg in args)
    assert not any(arg.startswith("--network=") or arg == "host" for arg in args)


def test_limits_are_configurable() -> None:
    args = " ".join(hardened_run_args("c", "i", limits=Limits(memory="128m", pids=32)))
    assert "--memory 128m" in args and "--memory-swap 128m" in args and "--pids-limit 32" in args


def test_wrap_script_streams_out_dir() -> None:
    script = wrap_script("echo hi")
    assert script.startswith("mkdir -p /tmp/out && ( echo hi\n) > /tmp/out/.log 2>&1;")
    assert script.endswith("tar -C /tmp/out -cf - .")
