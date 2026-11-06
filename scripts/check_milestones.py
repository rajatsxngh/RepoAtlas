#!/usr/bin/env python3
"""Docs lint for the RepoAtlas milestone guide (standard library only).

Checks:
  1. Every milestone file has all required sections.
  2. Its task count fits its declared size (S <= 5, M 6-8, L 9-12).
  3. "Depends on" names only lower-numbered milestones.
  4. No milestone file mentions a higher-numbered milestone, or a requirement owned by one.
  5. Every PRD requirement ID is defined once and listed in exactly its owning milestone's
     Requirements section; every ID a milestone cites exists in the PRD.
  6. The README status table matches each file's dependencies and size, and the mermaid
     chain lists every milestone in order.

Usage:  python scripts/check_milestones.py
Exit code 0 when everything passes, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MILESTONE_DIR = ROOT / "docs" / "milestones"
PRD_PATH = ROOT / "docs" / "PRD.md"
README_PATH = MILESTONE_DIR / "README.md"

PREFIX_OWNER = {
    "FND": 0, "ING": 1, "GRA": 2, "STO": 3, "API": 4, "MAP": 5,
    "RET": 6, "ANS": 7, "SBX": 8, "BEN": 9, "EVL": 10, "IMP": 11,
    "DEP": 12, "GRD": 13, "AGT": 14, "MAS": 15, "PRF": 16, "LCH": 17,
}
REQUIRED_SECTIONS = [
    "Goal", "Depends on", "Provides", "Requirements", "Scope", "Design notes",
    "Tasks", "Exit criteria", "Verification", "Risks and open questions", "Results",
]
SIZE_BOUNDS = {"S": (1, 5), "M": (6, 8), "L": (9, 12)}

REQ_RE = re.compile(r"\b(" + "|".join(PREFIX_OWNER) + r")-(\d+)\b")
REQ_DEF_RE = re.compile(r"^\|\s*((?:" + "|".join(PREFIX_OWNER) + r")-\d+)\s*\|", re.MULTILINE)
MS_RE = re.compile(r"\bM(\d{1,2})\b")
RANGE_RE = re.compile(r"\bM(\d{1,2})\s*[–—-]\s*M(\d{1,2})\b")
TASK_RE = re.compile(r"^- \[[ xX]\] \*\*T\d+", re.MULTILINE)
SIZE_RE = re.compile(r"\*\*Size:\*\*\s*([SML])\b")
FILE_RE = re.compile(r"^M(\d{2})-[a-z0-9-]+\.md$")

errors: list[str] = []


def err(where: str, message: str) -> None:
    errors.append(f"{where}: {message}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sections(text: str) -> dict[str, str]:
    """Split markdown into level-2 sections keyed by heading text."""
    result: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                result[current] = "\n".join(lines)
            current = line[3:].strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        result[current] = "\n".join(lines)
    return result


def milestone_refs(text: str) -> set[int]:
    """All milestone numbers mentioned in text, expanding ranges like M00-M11."""
    refs: set[int] = set()
    for start, end in RANGE_RE.findall(text):
        lo, hi = sorted((int(start), int(end)))
        refs.update(range(lo, hi + 1))
    refs.update(int(n) for n in MS_RE.findall(text))
    return refs


def fmt(numbers: set[int]) -> str:
    return ", ".join(f"M{n:02d}" for n in sorted(numbers)) or "none"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Milestone files -------------------------------------------------------------------
    files: dict[int, Path] = {}
    for path in sorted(MILESTONE_DIR.glob("M*.md")):
        match = FILE_RE.match(path.name)
        if not match:
            err(path.name, "file name must look like MNN-short-name.md")
            continue
        number = int(match.group(1))
        if number in files:
            err(path.name, f"duplicate milestone number M{number:02d}")
        files[number] = path
    if not files:
        print("No milestone files found.")
        return 1
    expected = set(range(max(files) + 1))
    if set(files) != expected:
        err("docs/milestones", f"milestone numbers must be contiguous; missing {fmt(expected - set(files))}")

    # PRD requirement definitions ------------------------------------------------------
    prd_ids: dict[str, int] = {}
    for req_id in REQ_DEF_RE.findall(read(PRD_PATH)):
        if req_id in prd_ids:
            err("docs/PRD.md", f"requirement {req_id} is defined more than once")
        prd_ids[req_id] = PREFIX_OWNER[req_id.split("-")[0]]

    depends: dict[int, set[int]] = {}
    sizes: dict[int, str] = {}

    for number, path in sorted(files.items()):
        where = f"docs/milestones/{path.name}"
        text = read(path)
        secs = sections(text)

        # 1. Sections
        missing = [name for name in REQUIRED_SECTIONS if name not in secs]
        if missing:
            err(where, f"missing sections: {', '.join(missing)}")

        # 2. Size vs task count
        size_match = SIZE_RE.search(text)
        if not size_match:
            err(where, "header must declare **Size:** S, M, or L")
        else:
            size = size_match.group(1)
            sizes[number] = size
            task_count = len(TASK_RE.findall(secs.get("Tasks", "")))
            lo, hi = SIZE_BOUNDS[size]
            if not lo <= task_count <= hi:
                err(where, f"size {size} needs {lo}-{hi} tasks, found {task_count}")

        # 3. Depends on
        dep_text = secs.get("Depends on", "")
        dep_refs = milestone_refs(dep_text)
        if not dep_refs and "none" not in dep_text.lower():
            err(where, "Depends on must list milestones or say None")
        if any(ref >= number for ref in dep_refs):
            err(where, f"Depends on may only name lower-numbered milestones; found {fmt({r for r in dep_refs if r >= number})}")
        depends[number] = dep_refs

        # 4. No forward mentions anywhere in the file
        forward = {ref for ref in milestone_refs(text) if ref > number}
        if forward:
            err(where, f"mentions later milestones: {fmt(forward)}")
        for prefix, digits in REQ_RE.findall(text):
            req_id = f"{prefix}-{digits}"
            owner = PREFIX_OWNER[prefix]
            if req_id not in prd_ids:
                err(where, f"cites {req_id}, which is not defined in the PRD")
            elif owner > number:
                err(where, f"cites {req_id}, owned by later milestone M{owner:02d}")

        # 5. Requirements section lists exactly the owned IDs
        listed = {f"{p}-{d}" for p, d in REQ_RE.findall(secs.get("Requirements", ""))}
        owned = {req_id for req_id, owner in prd_ids.items() if owner == number}
        if listed != owned:
            if owned - listed:
                err(where, f"Requirements section is missing {', '.join(sorted(owned - listed, key=_req_sort))}")
            if listed - owned:
                err(where, f"Requirements section lists IDs it doesn't own: {', '.join(sorted(listed - owned, key=_req_sort))}")
        if not owned:
            err(where, "owns no PRD requirements")

    # 6. README table and mermaid chain ---------------------------------------------------
    readme = read(README_PATH)
    table_rows: dict[int, list[str]] = {}
    for line in readme.splitlines():
        row = re.match(r"^\|\s*M(\d{2})\s*\|", line)
        if row:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            table_rows[int(row.group(1))] = cells
    for number in sorted(files):
        where = f"docs/milestones/README.md (row M{number:02d})"
        cells = table_rows.get(number)
        if cells is None:
            err(where, "missing from the status table")
            continue
        if len(cells) < 6:
            err(where, "expected columns: Milestone | Name | Type | Size | Depends on | Status")
            continue
        table_deps = set() if cells[4] in {"—", "-", ""} else milestone_refs(cells[4])
        if table_deps != depends.get(number, set()):
            err(where, f"Depends on {fmt(table_deps)} but the milestone file says {fmt(depends.get(number, set()))}")
        if number in sizes and cells[3] != sizes[number]:
            err(where, f"size {cells[3]} but the milestone file says {sizes[number]}")
        if files[number].name not in cells[1]:
            err(where, f"name cell should link to {files[number].name}")

    mermaid = re.search(r"```mermaid\n(.*?)```", readme, re.DOTALL)
    if not mermaid:
        err("docs/milestones/README.md", "missing the mermaid chain")
    else:
        order: list[int] = []
        for n in MS_RE.findall(mermaid.group(1)):
            if int(n) not in order:
                order.append(int(n))
        if order != sorted(files):
            err("docs/milestones/README.md", "mermaid chain must list every milestone once, in order")

    # Report ---------------------------------------------------------------------------------
    if errors:
        print(f"FAILED: {len(errors)} problem(s)")
        for line in errors:
            print(f"  - {line}")
        return 1
    print(f"OK: {len(files)} milestones, {len(prd_ids)} requirements, dependencies point backward only.")
    return 0


def _req_sort(req_id: str) -> tuple[int, int]:
    prefix, digits = req_id.split("-")
    return PREFIX_OWNER[prefix], int(digits)


if __name__ == "__main__":
    sys.exit(main())
