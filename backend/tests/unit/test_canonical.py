from __future__ import annotations

from pathlib import Path

from repoatlas.canonical import canonical_json, digest, pretty_json, write_text_lf
from repoatlas.parsing.models import Range


def test_canonical_json_is_sorted_compact_and_keeps_unicode() -> None:
    assert canonical_json({"b": 1, "a": ["ü", {"d": 2, "c": 3}]}) == (
        '{"a":["ü",{"c":3,"d":2}],"b":1}'
    )


def test_models_serialize_canonically() -> None:
    value = Range(start_line=1, start_col=0, end_line=2, end_col=5)
    assert canonical_json(value) == '{"end_col":5,"end_line":2,"start_col":0,"start_line":1}'


def test_digest_depends_only_on_content() -> None:
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})
    assert len(digest([])) == 64


def test_pretty_json_ends_with_a_newline() -> None:
    assert pretty_json({"b": 1, "a": 2}) == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_write_text_lf_never_writes_crlf(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out.json"
    write_text_lf(target, "line one\nline two\n")
    assert target.read_bytes() == b"line one\nline two\n"
