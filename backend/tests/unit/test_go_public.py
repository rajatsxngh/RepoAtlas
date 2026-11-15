"""Go-public readiness (LCH-1 as a build gate, decision 0025): the checklist says nothing is
public yet, and the live pass lists every item as pending, with an order for the owner."""

from __future__ import annotations

import re

import pytest

from tests.unit.launch_numbers import LAUNCH, ROOT, read

pytestmark = pytest.mark.unit


def test_the_checklist_says_it_is_not_done_and_covers_the_gate_and_the_caps() -> None:
    checklist = read(LAUNCH / "go-public.md")

    assert "Status: not done." in checklist
    assert "RepoAtlas has never been deployed or made public" in checklist
    for step in ("REPOATLAS_ACCESS_TOKEN", "REPOATLAS_DAILY_SPEND_CAP_USD", "Putting the gate"):
        assert step in checklist
    assert "infra/production.env.example" in checklist
    numbered = re.findall(r"^(\d+)\. ", checklist, re.MULTILINE)
    assert numbered == [str(number) for number in range(1, 15)]


def test_the_live_pass_lists_every_item_as_pending_with_an_order() -> None:
    text = read(ROOT / "docs" / "live-pass.md")
    rows = [line for line in text.splitlines() if re.match(r"\| \d+ \| M\d\d \|", line)]

    assert [int(row.split("|")[1]) for row in rows] == list(range(1, 31))
    assert all(row.rstrip().endswith("| Pending |") for row in rows)
    assert sum("| M17 |" in row for row in rows) == 7
    order = text.index("## Live pass order")
    assert order < text.index("## All items") < text.index("## How to run them")
    for heading in ("### A. What a public launch needs", "### B. Recommended", "### C. Optional"):
        assert heading in text
    assert "_None yet." in text.split("## Results")[-1]  # nothing has been run


def test_the_order_names_only_items_that_exist_and_covers_them_all() -> None:
    text = read(ROOT / "docs" / "live-pass.md")
    section = text.split("## Live pass order")[1].split("## All items")[0]
    named: set[int] = set()
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        # Section A rows are "| step | items | … |"; B and C rows are "| items | … |".
        if len(cells) < 4 or not re.fullmatch(r"[\d, ]+", cells[1]):
            continue
        items = cells[2] if re.fullmatch(r"[\d, ]+", cells[2]) else cells[1]
        named.update(int(number) for number in items.split(","))

    assert named == set(range(1, 31))
