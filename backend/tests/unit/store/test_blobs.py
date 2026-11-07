from __future__ import annotations

import pytest

from repoatlas.parsing.models import Range
from repoatlas.store.blobs import decode_blob, line_count, split_lines
from repoatlas.store.rows import callee_name, range_from, range_values


@pytest.mark.parametrize(
    ("text", "lines"),
    [
        ("", []),
        ("a", ["a"]),
        ("a\n", ["a"]),
        ("a\n\n", ["a", ""]),
        ("a\nb", ["a", "b"]),
        ("\n", [""]),
        ("a\r\nb\r\n", ["a\r", "b\r"]),
        ("a\x0cb\u2028c\rd", ["a\x0cb\u2028c\rd"]),  # only \n starts a line, as in tree-sitter
    ],
)
def test_lines_split_on_newlines_only(text: str, lines: list[str]) -> None:
    assert split_lines(text) == lines
    assert line_count(text) == len(lines)


def test_valid_utf8_is_kept_exactly() -> None:
    data = "﻿def café():\r\n    pass\n".encode()
    decoded = decode_blob(data)
    assert decoded.text == data.decode("utf-8")
    assert (decoded.lossy, decoded.line_count) == (False, 2)


def test_invalid_bytes_and_nul_become_replacement_characters() -> None:
    decoded = decode_blob(b"a = '\xff\xfe'\n\x00b\n")
    assert decoded.text == "a = '��'\n�b\n"
    assert (decoded.lossy, decoded.line_count) == (True, 2)


def test_a_split_multibyte_sequence_never_eats_a_newline() -> None:
    decoded = decode_blob(b"x\xe2\x82\ny\n")  # a truncated three-byte sequence before \n
    assert decoded.lossy and decoded.line_count == 2
    assert split_lines(decoded.text)[1] == "y"


@pytest.mark.parametrize(
    ("callee", "name"),
    [
        ("f", "f"),
        ("self.items.append", "append"),
        ("app.get", "get"),
        ("$", "$"),
        ("jQuery.$ajax", "$ajax"),
        ("obj.é_t2", "é_t2"),
        ("handlers[k]", None),
        ("make()", None),
        ("a.b().c", "c"),
    ],
)
def test_callee_name_is_the_last_identifier(callee: str, name: str | None) -> None:
    assert callee_name(callee) == name


def test_ranges_round_trip_through_columns() -> None:
    value = Range(start_line=3, start_col=4, end_line=5, end_col=0)
    assert range_from(range_values(value)) == value
    assert range_values(None) == (None, None, None, None)
    assert range_from((None, None, None, None)) is None
