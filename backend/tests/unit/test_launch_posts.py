"""The launch post drafts (LCH-6, decision 0025) quote only numbers that are in the report
files, and they are drafts: no address is made up and nothing claims the launch happened."""

from __future__ import annotations

import re

import pytest

from tests.unit.launch_numbers import LAUNCH, measured, read

pytestmark = pytest.mark.unit

INTERVAL = re.compile(r"[+\-]?\d\.\d{3} \[[^\]]+\]")
OWN_REPO = r"https?://(?!github\.com/rajatsxngh/RepoAtlas)\S+"


def test_the_post_quotes_the_reports() -> None:
    post = read(LAUNCH / "linkedin-post.md")
    numbers = measured()

    assert numbers["questions"] == "90" and "90 questions" in post
    for key in ("hybrid", "vector", "gain", "multihop", "real_recall", "memory"):
        assert numbers[key] in post, (key, numbers[key])
    assert numbers["cost"] == "$0.0220" and "about 2 cents" in post
    # Every bracketed interval in the post is one of the measured ones.
    allowed = {numbers[key] for key in ("hybrid", "gain", "multihop", "real_recall")}
    found = INTERVAL.findall(post)
    assert found and set(found) <= allowed


def test_the_post_is_a_draft_that_does_not_claim_the_launch() -> None:
    post = read(LAUNCH / "linkedin-post.md")

    assert "Status: a draft for the owner to edit and publish." in post
    assert "Nothing has been posted." in post
    assert post.count("<PUBLIC URL>") >= 3  # never a made-up address
    assert "Not evaluated yet: the agent and multi-agent modes" in post
    assert "DeepWiki" in post and "Understand-Anything" in post
    assert not re.search(OWN_REPO, post)


def test_the_other_drafts_follow_the_same_rules() -> None:
    drafts = read(LAUNCH / "other-channels.md")
    numbers = measured()

    assert "Status: drafts for the owner." in drafts and "Nothing has been posted." in drafts
    allowed = {numbers[key] for key in ("hybrid", "gain", "multihop", "real_recall")}
    found = INTERVAL.findall(drafts)
    assert found and set(found) <= allowed
    assert "<PUBLIC URL>" in drafts and not re.search(OWN_REPO, drafts)
    assert "not evaluated yet" in drafts
    assert "DeepWiki" in drafts and "Understand-Anything" in drafts
