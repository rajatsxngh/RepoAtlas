"""Test reports → ``TestResult`` rows, and test IDs → test symbol keys (SBX-9, SBX-10).

Test IDs use the runners' own spelling, which ``parse_file`` also records as ``runner_id``:

- pytest: ``path::Class::test_name[param]``
- Vitest/Jest: ``path > describe > it``

Suite-level problems (pytest collection errors, a Vitest/Jest file that fails before any test
runs) become rows with ``collection=True`` and the file path as the ID; they are never ground
truth.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from repoatlas.parsing.models import ParsedFile

Outcome = Literal["passed", "failed", "error", "skipped"]
_PARAM_RE = re.compile(r"\[.*\]$")


class TestResult(BaseModel):
    __test__ = False  # not a pytest test class
    model_config = ConfigDict(frozen=True, extra="forbid")

    test_id: str
    outcome: Outcome
    duration: float = 0.0
    collection: bool = False


class ReportError(ValueError):
    """The report file is missing or unreadable."""


# --------------------------------------------------------------------------------------------
# JUnit XML (pytest)
# --------------------------------------------------------------------------------------------


def _module_of(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".")


def _split_classname(classname: str, known_paths: Iterable[str]) -> tuple[str, list[str]]:
    """``tests.test_x.TestA`` → (``tests/test_x.py``, [``TestA``]) using known test files."""
    best: tuple[str, list[str]] | None = None
    for path in known_paths:
        module = _module_of(path)
        if classname == module or classname.startswith(module + "."):
            rest = classname[len(module) + 1 :]
            candidate = (path, rest.split(".") if rest else [])
            if best is None or len(module) > len(_module_of(best[0])):
                best = candidate
    if best is not None:
        return best
    return classname.replace(".", "/") + ".py", []


def parse_junit(data: bytes, *, known_paths: Iterable[str] = ()) -> list[TestResult]:
    """pytest JUnit XML: ``xunit1`` has a ``file`` attribute; ``xunit2`` needs ``known_paths``."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ReportError(f"JUnit report is not valid XML: {exc}") from exc
    paths = list(known_paths)
    results: list[TestResult] = []
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        file_attr = case.get("file")
        children = {child.tag for child in case}
        errors = [child for child in case if child.tag == "error"]
        is_collection = any("collection failure" in (e.get("message") or "") for e in errors) or (
            not classname and "error" in children
        )
        if "failure" in children:
            outcome: Outcome = "failed"
        elif "error" in children:
            outcome = "error"
        elif "skipped" in children:
            outcome = "skipped"
        else:
            outcome = "passed"
        if is_collection:
            path = file_attr or (name.replace(".", "/") + ".py" if "/" not in name else name)
            results.append(TestResult(test_id=path, outcome="error", collection=True))
            continue
        if file_attr:
            module = _module_of(file_attr)
            rest = classname[len(module) + 1 :] if classname.startswith(module + ".") else ""
            path, classes = file_attr, rest.split(".") if rest else []
        else:
            path, classes = _split_classname(classname, paths)
        results.append(
            TestResult(
                test_id="::".join([path, *classes, name]),
                outcome=outcome,
                duration=round(float(case.get("time") or 0.0), 4),
            )
        )
    return _merge(results)


# --------------------------------------------------------------------------------------------
# Vitest / Jest JSON
# --------------------------------------------------------------------------------------------

_JSON_OUTCOMES: dict[str, Outcome] = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "pending": "skipped",
    "todo": "skipped",
    "disabled": "skipped",
    "focused": "passed",
}


def _relative(path: str, roots: Iterable[str]) -> str:
    for root in roots:
        prefix = root.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path.lstrip("/")


def parse_jest_json(
    data: bytes, *, roots: Iterable[str] = ("/src", "/tmp/work")
) -> list[TestResult]:
    """Vitest's ``--reporter=json`` and Jest's ``--json`` (the same shape)."""
    try:
        report: Any = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ReportError(f"JSON report is not valid JSON: {exc}") from exc
    roots = list(roots)
    results: list[TestResult] = []
    for suite in report.get("testResults", []):
        path = _relative(str(suite.get("name", "")), roots)
        assertions = suite.get("assertionResults") or []
        failed_assertions = any(a.get("status") == "failed" for a in assertions)
        if suite.get("status") == "failed" and not failed_assertions:
            results.append(TestResult(test_id=path, outcome="error", collection=True))
        for item in assertions:
            titles = [str(t) for t in item.get("ancestorTitles") or []]
            title = str(item.get("title", ""))
            duration_ms = item.get("duration") or 0
            results.append(
                TestResult(
                    test_id=" > ".join([path, *titles, title]),
                    outcome=_JSON_OUTCOMES.get(str(item.get("status")), "error"),
                    duration=round(float(duration_ms) / 1000, 4),
                )
            )
    return _merge(results)


_RANK: dict[Outcome, int] = {"passed": 0, "skipped": 1, "error": 2, "failed": 3}


def _merge(results: list[TestResult]) -> list[TestResult]:
    """One row per test ID (duplicates keep the worst outcome), sorted by ID."""
    merged: dict[tuple[str, bool], TestResult] = {}
    for result in results:
        key = (result.test_id, result.collection)
        current = merged.get(key)
        if current is None or _RANK[result.outcome] > _RANK[current.outcome]:
            merged[key] = result
    return sorted(merged.values(), key=lambda r: (r.test_id, r.collection))


def parse_report(
    reporter: str, data: bytes, *, known_paths: Iterable[str] = ()
) -> list[TestResult]:
    if reporter == "junit":
        return parse_junit(data, known_paths=known_paths)
    if reporter in ("vitest-json", "jest-json"):
        return parse_jest_json(data)
    raise ValueError(f"Unknown reporter {reporter!r}")


# --------------------------------------------------------------------------------------------
# Test ID → test symbol key
# --------------------------------------------------------------------------------------------


def normalize_test_id(test_id: str) -> str:
    """Drop pytest parameters: ``a.py::T::test[1-x]`` → ``a.py::T::test``."""
    if "::" in test_id:
        return _PARAM_RE.sub("", test_id)
    return test_id


class TestIndex:
    """Maps runner test IDs to the test symbol keys ``parse_file`` assigned."""

    __test__ = False

    def __init__(self, parsed_files: Iterable[ParsedFile] | Mapping[str, ParsedFile]) -> None:
        files = parsed_files.values() if isinstance(parsed_files, Mapping) else parsed_files
        self._keys: dict[str, str] = {}
        for parsed in files:
            for case in parsed.tests:
                self._keys.setdefault(case.runner_id, case.key)

    def map(self, test_id: str) -> str | None:
        """``None`` for tests ``parse_file`` does not extract (for example ``it.each`` rows)."""
        return self._keys.get(normalize_test_id(test_id))


def map_test_id(test_id: str, parsed_files: Iterable[ParsedFile]) -> str | None:
    """The test symbol key for one runner test ID, or ``None`` when it cannot be mapped."""
    return TestIndex(parsed_files).map(test_id)
