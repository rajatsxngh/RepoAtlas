"""The demo (LCH-5, decision 0025): the script says what is not recorded, the recording that
exists is a real GIF of a size a README can carry, and the recorder never asks a model."""

from __future__ import annotations

import struct

import pytest

from tests.unit.launch_numbers import LAUNCH, ROOT, read

pytestmark = pytest.mark.unit

RECORDER = ROOT / "frontend" / "scripts" / "record-demo.mjs"


def test_the_script_says_what_is_recorded_and_what_is_not() -> None:
    script = read(LAUNCH / "demo-script.md")

    assert "Status: not recorded." in script  # the full demo, with a live answer
    assert "It does **not** show a question being answered" in script
    assert "No stand-in answer appears in\nit." in script
    assert "node scripts/record-demo.mjs" in script
    times = [line.split("|")[1].strip() for line in script.splitlines() if line.startswith("| 0:")]
    assert times[0].startswith("0:00") and times[-1].endswith("0:45")  # 45 seconds


def test_the_recording_is_a_gif_a_readme_can_carry() -> None:
    data = (LAUNCH / "demo.gif").read_bytes()

    assert data[:6] == b"GIF89a"
    width, height = struct.unpack("<HH", data[6:10])
    assert (width, height) == (800, 500)
    assert 200_000 < len(data) < 4_000_000
    assert data.count(b"\x21\xf9\x04") > 50  # frames: it is an animation, not a still


def test_the_recorder_never_asks_a_model() -> None:
    source = read(RECORDER)

    # It clicks neither Ask nor Explain, and fails the recording if such a request is seen.
    assert not any(name in source for name in ('name: "Ask"', 'name: "Explain"', "Ask a question"))
    assert r"/\/(ask|explain)$/" in source
    assert "The recording asked a model" in source
