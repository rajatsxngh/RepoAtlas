"""The parse cache (PRF-2, decision 0024): parse results shared across snapshots.

``parse_file`` reads a file's path, language, test flag, and bytes, and nothing else. Its
result is stored under a digest of exactly those inputs (the bytes by their blob OID) and the
parser component versions, so a file that did not change between two commits is parsed once,
whichever snapshot asked first. A changed parser version changes every key; nothing is ever
updated in place.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue
from sqlalchemy import Engine, text

from repoatlas.canonical import digest
from repoatlas.ingest.inventory import FileEntry
from repoatlas.parsing.models import ParsedFile

INSERT_BATCH = 200


def parse_key(entry: FileEntry, components: Mapping[str, JsonValue]) -> str:
    """The cache key of ``entry``'s parse result under the parser ``components`` (the versions
    the ``parse`` stage puts in the config fingerprint)."""
    return digest(
        {
            "path": entry.path,
            "blob_oid": entry.blob_oid,
            "language": entry.language,
            "is_test": entry.is_test,
            "components": dict(components),
        }
    )


def load_parsed(keys: Sequence[str], *, engine: Engine) -> dict[str, ParsedFile]:
    """The cached parse results among ``keys``, by key. A row that no longer reads back as a
    ``ParsedFile`` is treated as missing."""
    if not keys:
        return {}
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT key, parsed FROM parse_cache WHERE key = ANY(:keys)"),
            {"keys": list(keys)},
        ).all()
    found: dict[str, ParsedFile] = {}
    for row in rows:
        try:
            found[row.key] = ParsedFile.model_validate_json(row.parsed)
        except ValueError:  # written by a model this code no longer accepts
            continue
    return found


def save_parsed(items: Mapping[str, ParsedFile], *, engine: Engine) -> None:
    """Store parse results by key; a key that is already there is left alone."""
    rows = [
        {
            "key": key,
            "path": parsed.path,
            "blob_oid": parsed.blob_oid,
            "parsed": parsed.model_dump_json(),
        }
        for key, parsed in items.items()
    ]
    insert = text(
        "INSERT INTO parse_cache (key, path, blob_oid, parsed) "
        "VALUES (:key, :path, :blob_oid, :parsed) ON CONFLICT (key) DO NOTHING"
    )
    for offset in range(0, len(rows), INSERT_BATCH):
        with engine.begin() as connection:
            connection.execute(insert, rows[offset : offset + INSERT_BATCH])
