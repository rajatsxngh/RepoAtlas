"""The README (LCH-4, decision 0025) quotes only numbers that are in the report files, says
that nothing is launched, and names what is pending instead of claiming it."""

from __future__ import annotations

import pytest
import yaml

from tests.unit.launch_numbers import ROOT, interval, measured, read, report

pytestmark = pytest.mark.unit

README = read(ROOT / "README.md")


def test_the_results_table_is_the_reports() -> None:
    numbers = measured()

    for system, label in (
        ("closed_book", "No evidence (closed book)"),
        ("bm25_only", "Keyword search"),
        ("vector_only", "Vector search"),
    ):
        data = report(f"2026-11-12-{system}-test.json")
        row = f"| {label} | {interval(data['overall'])} | ${data['cost_per_question_usd']:.4f} |"
        assert row in README, row
    assert f"**{numbers['hybrid']}** | {numbers['cost']} |" in README
    for key in ("gain", "multihop", "real_recall"):
        assert numbers[key] in README, key
    impact = report("2026-11-13-impact_engine-intervals-test.json")["intervals"]
    by_metric = {item["metric"]: item for item in impact}
    assert f"precision {interval(by_metric['static_precision'])}" in README
    assert f"recall {interval(by_metric['static_recall'])}" in README


def test_it_says_what_is_built_and_what_is_pending() -> None:
    assert "Status (2026-11-16): built, not yet launched." in README
    assert "has not been deployed anywhere and has no public URL yet" in README
    assert "shows only features that use no model" in README
    pending = README.split("**Not measured yet**")[1].split("## Run it yourself")[0]
    for item in ("agent and multi-agent", "hardened prompt", "injection", "final evaluation"):
        assert item in pending, item


def test_it_has_the_parts_a_launch_readme_needs() -> None:
    assert README.index("docs/launch/demo.gif") < README.index("## What it does")  # at the top
    assert "```mermaid" in README  # the architecture diagram
    for link in ("docs/results.md", "docs/self-hosting.md", "docs/live-pass.md", "LICENSE"):
        assert f"({link})" in README, link
    assert "DeepWiki" in README and "Understand-Anything" in README
    assert "Cost per question" in README


def test_it_names_every_gallery_repo() -> None:
    listing = yaml.safe_load(read(ROOT / "launch" / "repos.yaml"))
    titles = [entry["title"] for entry in listing["repos"]]

    assert len(titles) == 10
    for title in titles:
        assert title in README, title
