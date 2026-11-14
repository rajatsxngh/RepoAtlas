"""Allowlist helpers (GRD-7): only snapshot files, only valid arguments."""

from __future__ import annotations

import pytest

from repoatlas.guardrails.allowlist import (
    MAX_KEY_CHARS,
    MAX_PATH_CHARS,
    SnapshotFile,
    ensure_snapshot_path,
    path_problem,
    validate_blob_oid,
    validate_limit,
    validate_line_range,
    validate_symbol_key,
    validate_text,
)
from repoatlas.store.errors import InvalidQuery, PathNotInSnapshot

pytestmark = pytest.mark.injection_static

FILES = {
    "src/app.py": SnapshotFile(1, "src/app.py", "a" * 40, 17, None),
    "assets/logo.png": SnapshotFile(2, "assets/logo.png", "b" * 40, None, "binary"),
    "src/ignore_previous_instructions.py": SnapshotFile(3, "x", "c" * 40, 5, None),
}
HOSTILE_PATHS = [
    "",
    "/etc/passwd",
    "../secrets.env",
    "src/../../etc/passwd",
    "src/./app.py",
    "src//app.py",
    "src/app.py/",
    "./src/app.py",
    "..",
    "C:/Windows/win.ini",
    "c:\\Windows\\win.ini",
    "src\\app.py",
    "..\\..\\secrets",
    "src/app.py\x00.png",
    "src/app.py\nX-Injected: 1",
    "src/\x1b[31mapp.py",
    "a" * (MAX_PATH_CHARS + 1),
    "~/.ssh/id_rsa/..",
    "file:///etc/passwd/..",
]


class Lookup:
    """A snapshot's file list, counting how often it is asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def __call__(self, snapshot_id: int, path: str) -> SnapshotFile | None:
        self.calls.append((snapshot_id, path))
        return FILES.get(path) if snapshot_id == 7 else None


def test_a_file_of_the_snapshot_is_returned() -> None:
    lookup = Lookup()
    assert ensure_snapshot_path(7, "src/app.py", lookup=lookup) is FILES["src/app.py"]
    assert ensure_snapshot_path(7, "assets/logo.png", lookup=lookup).line_count is None
    # A file whose name is an instruction is still just a file.
    assert ensure_snapshot_path(7, "src/ignore_previous_instructions.py", lookup=lookup).id == 3
    assert lookup.calls == [
        (7, "src/app.py"),
        (7, "assets/logo.png"),
        (7, "src/ignore_previous_instructions.py"),
    ]


@pytest.mark.parametrize("path", HOSTILE_PATHS)
def test_a_malformed_path_is_refused_before_any_lookup(path: str) -> None:
    lookup = Lookup()
    assert path_problem(path) is not None
    with pytest.raises(PathNotInSnapshot) as caught:
        ensure_snapshot_path(7, path, lookup=lookup)
    assert lookup.calls == []  # the file list was never consulted
    assert len(str(caught.value)) < 400  # a huge path is not echoed back whole


def test_a_well_formed_path_outside_the_snapshot_is_refused() -> None:
    lookup = Lookup()
    for snapshot_id, path in ((7, "src/other.py"), (7, "etc/passwd"), (8, "src/app.py")):
        with pytest.raises(PathNotInSnapshot, match="has no file"):
            ensure_snapshot_path(snapshot_id, path, lookup=lookup)
    assert len(lookup.calls) == 3
    # Paths are keys, compared exactly: no case folding, no normalization.
    for near in ("SRC/app.py", "src/App.py", "src/app.py ", " src/app.py"):
        with pytest.raises(PathNotInSnapshot):
            ensure_snapshot_path(7, near, lookup=lookup)


@pytest.mark.parametrize(
    "path",
    ["src/app.py", "a", "weird name (1).py", "src/<svg onload=x>.ts", "ünï/cödé.py", "a..b/c"],
)
def test_ordinary_and_odd_but_valid_paths_pass_the_shape_check(path: str) -> None:
    assert path_problem(path) is None


def test_line_ranges_are_clamped_to_the_file_and_the_read_limit() -> None:
    assert validate_line_range(3, 9, line_count=17) == (3, 9)
    assert validate_line_range(-5, 4, line_count=17) == (1, 4)
    assert validate_line_range(10, 10_000, line_count=17) == (10, 17)
    assert validate_line_range(1, 10_000, line_count=5_000, max_lines=400) == (1, 400)
    assert validate_line_range(100, 900, line_count=5_000, max_lines=400) == (100, 499)
    assert validate_line_range(5, 5) == (5, 5)
    assert validate_line_range(1, 2**40) == (1, 2**31 - 1)


@pytest.mark.parametrize(
    ("start", "end", "kwargs", "message"),
    [
        (9, 3, {"line_count": 17}, "ends before it starts"),
        (18, 20, {"line_count": 17, "path": "src/app.py"}, "past the end of src/app.py (17"),
        (1, 5, {"line_count": 0}, "past the end"),
        (-9, -2, {"line_count": 17}, "Lines start at 1"),
        (1, 5, {"max_lines": 0}, "At least one line"),
        (True, 5, {}, "integers"),
    ],
)
def test_invalid_line_ranges(start: int, end: int, kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(InvalidQuery, match=message.replace("(", r"\(")):
        validate_line_range(start, end, **kwargs)  # type: ignore[arg-type]


def test_symbol_keys() -> None:
    for key in (
        "py:src/app.py:create_app:function",
        "ts:web/src/a.ts:K.run:method~2",
        "file:src/app.py",
        "dir:src",
        "route:src/app.py:GET %2Fitems",
    ):
        assert validate_symbol_key(key) == key
    for bad in ("", "create_app", "../etc/passwd", "py:a\x00b", "sql:x", "x" * (MAX_KEY_CHARS + 1)):
        with pytest.raises(InvalidQuery):
            validate_symbol_key(bad)


def test_blob_oids_limits_and_text() -> None:
    assert validate_blob_oid("a" * 40) == "a" * 40
    for bad in ("a" * 39, "A" * 40, "g" * 40, "a" * 40 + "' OR 1=1 --", "", "a" * 41):
        with pytest.raises(InvalidQuery):
            validate_blob_oid(bad)
    assert validate_limit(5, maximum=50) == 5
    assert validate_limit(0, maximum=3, minimum=0, name="depth") == 0
    for bad_limit in (0, 51, -1, True):
        with pytest.raises(InvalidQuery):
            validate_limit(bad_limit, maximum=50)
    with pytest.raises(InvalidQuery, match="depth must be between 0 and 3"):
        validate_limit(4, maximum=3, minimum=0, name="depth")
    with pytest.raises(InvalidQuery, match="is an integer"):
        validate_limit("5", maximum=50)  # type: ignore[arg-type]
    assert validate_text("  how does it work?  ", max_chars=100) == "how does it work?"
    for bad_text in ("", "   ", "x" * 101, "a\x00b"):
        with pytest.raises(InvalidQuery):
            validate_text(bad_text, max_chars=100, name="question")
    with pytest.raises(InvalidQuery, match="question is text"):
        validate_text(5, max_chars=100, name="question")  # type: ignore[arg-type]
