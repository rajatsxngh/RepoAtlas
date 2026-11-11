"""``parse_file`` over a recipe's source tree: the parse results mutations and mappings use."""

from __future__ import annotations

from repoatlas.ingest.inventory import FileEntry, build_inventory, is_test_path, language_for
from repoatlas.ingest.source import SourceTree, blob_oid
from repoatlas.parsing import parse_file
from repoatlas.parsing.models import ParsedFile

# The benchmark repos are curated; these caps only guard against a wrong recipe.
MAX_FILES = 20_000
MAX_FILE_BYTES = 2_000_000


def parse_bytes(path: str, data: bytes) -> ParsedFile:
    """Parse one file's bytes as if it had been inventoried (``ValueError`` if not source)."""
    language = language_for(path)
    if language is None:
        raise ValueError(f"{path} is not a Python or TypeScript/JavaScript file")
    entry = FileEntry(
        path=path,
        mode="100644",
        blob_oid=blob_oid(data),
        size=len(data),
        language=language,
        is_test=is_test_path(path, language),
        loc=None,
        skip_reason=None,
    )
    return parse_file(entry, data)


def parse_tree(tree: SourceTree) -> dict[str, ParsedFile]:
    """Every parseable file in the tree, keyed by path (inventory skip rules apply)."""
    inventory = build_inventory(tree, max_source_files=MAX_FILES, max_file_bytes=MAX_FILE_BYTES)
    by_path = {entry.path: entry for entry in tree.entries()}
    parsed: dict[str, ParsedFile] = {}
    for entry in inventory.files:
        if entry.skip_reason is not None:
            continue
        parsed[entry.path] = parse_file(entry, tree.read(by_path[entry.path]))
    return parsed
