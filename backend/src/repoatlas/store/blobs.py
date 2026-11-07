"""File text as the store keeps it: UTF-8 text with the original line breaks (STO-2).

Lines are what tree-sitter counts as rows: text separated by ``\\n`` only, so a ``\\r`` stays at
the end of its line and form feeds or Unicode line separators never start a new one. A final
newline ends the last line instead of starting an empty one, as editors show it.
"""

from __future__ import annotations

from dataclasses import dataclass

REPLACEMENT = "�"


@dataclass(frozen=True, slots=True)
class BlobText:
    text: str
    lossy: bool
    line_count: int


def decode_blob(data: bytes) -> BlobText:
    """Decode a blob for storage. Invalid UTF-8 and NUL characters (which Postgres text cannot
    hold) become U+FFFD and mark the text ``lossy``; neither can touch a newline byte, so line
    numbers stay the same."""
    try:
        text = data.decode("utf-8")
        lossy = False
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        lossy = True
    if "\0" in text:
        text = text.replace("\0", REPLACEMENT)
        lossy = True
    return BlobText(text=text, lossy=lossy, line_count=line_count(text))


def line_count(text: str) -> int:
    """Number of lines: ``""`` has 0, ``"a"`` and ``"a\\n"`` have 1, ``"a\\n\\n"`` has 2."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def split_lines(text: str) -> list[str]:
    """The lines of ``text`` without their ``\\n`` (``len(split_lines(t)) == line_count(t)``)."""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines
