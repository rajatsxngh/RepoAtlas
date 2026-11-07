"""Helpers for extractor tests: parse a snippet as if it were an inventoried file."""

from __future__ import annotations

from repoatlas.ingest.inventory import FileEntry, is_test_path, language_for
from repoatlas.ingest.source import blob_oid
from repoatlas.parsing import parse_file
from repoatlas.parsing.models import ParsedFile, Symbol


def parse_source(code: str | bytes, path: str, *, is_test: bool | None = None) -> ParsedFile:
    data = code.encode("utf-8") if isinstance(code, str) else code
    language = language_for(path)
    assert language is not None, path
    entry = FileEntry(
        path=path,
        mode="100644",
        blob_oid=blob_oid(data),
        size=len(data),
        language=language,
        is_test=is_test_path(path, language) if is_test is None else is_test,
        loc=None,
        skip_reason=None,
    )
    return parse_file(entry, data)


def symbol(parsed: ParsedFile, qualname: str, ordinal: int = 1) -> Symbol:
    matches = [s for s in parsed.symbols if s.qualname == qualname]
    assert len(matches) >= ordinal, (
        f"{qualname} not found in {[s.qualname for s in parsed.symbols]}"
    )
    return matches[ordinal - 1]


def keys(parsed: ParsedFile) -> list[str]:
    return [s.key for s in parsed.symbols]
