"""Fetch (or read) a repository, build its inventory, and parse every indexed file (ING-12)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from repoatlas.canonical import digest
from repoatlas.config import Settings
from repoatlas.ingest.fetch import Workspace, fetch_source
from repoatlas.ingest.github import GitHubClient, check_repo_size, parse_repo_url
from repoatlas.ingest.inventory import FileEntry, Inventory, build_inventory
from repoatlas.ingest.source import DirectorySourceTree, SourceTree, TreeEntry
from repoatlas.parsing import parse_file
from repoatlas.parsing.models import ParsedFile


class ParseStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    files_parsed: int
    files_with_issues: int
    symbols: int
    imports: int
    calls: int
    tests: int
    routes: int


class ParseOutput(BaseModel):
    """Everything ``repoatlas parse`` produces; ``digest`` covers all other fields."""

    model_config = ConfigDict(frozen=True)

    source: str
    commit: str | None
    inventory: Inventory
    files: list[ParsedFile]
    stats: ParseStats
    digest: str


class ParseCache(Protocol):
    """Parse results kept between builds, by key (``repoatlas.store.parsecache``)."""

    def key(self, entry: FileEntry) -> str: ...

    def load(self, keys: Sequence[str]) -> Mapping[str, ParsedFile]: ...

    def save(self, items: Mapping[str, ParsedFile]) -> None: ...


@dataclass
class CacheUse:
    """How many files a parse took from the cache (``hits``) and parsed (``misses``)."""

    hits: int = 0
    misses: int = 0

    def as_json(self) -> dict[str, JsonValue]:
        return {"hits": self.hits, "misses": self.misses}


def parse_files(
    tree: SourceTree,
    inventory: Inventory,
    *,
    progress: Callable[[int, int], None] | None = None,
    cache: ParseCache | None = None,
    use: CacheUse | None = None,
) -> list[ParsedFile]:
    """Parse every indexed file of ``inventory`` (read from ``tree``) in path order;
    ``progress(done, total)`` is called after each file.

    With a ``cache``, a file whose parse result is cached is not read or parsed again, and new
    results are saved to it; ``use`` receives the counts. A cached result is used only if it
    names the same path and blob."""
    entries = inventory.indexed
    keys = {entry.path: cache.key(entry) for entry in entries} if cache is not None else {}
    cached = cache.load(list(keys.values())) if cache is not None else {}
    parsed: list[ParsedFile] = []
    fresh: dict[str, ParsedFile] = {}
    for done, entry in enumerate(entries, start=1):
        hit = cached.get(keys[entry.path]) if cache is not None else None
        if hit is not None and (hit.path, hit.blob_oid) == (entry.path, entry.blob_oid):
            parsed.append(hit)
        else:
            data = tree.read(TreeEntry(entry.path, entry.mode, entry.blob_oid, entry.size))
            result = parse_file(entry, data)
            parsed.append(result)
            if cache is not None:
                fresh[keys[entry.path]] = result
        if progress is not None:
            progress(done, len(entries))
    if cache is not None:
        cache.save(fresh)
    if use is not None:
        use.misses = len(fresh) if cache is not None else len(entries)
        use.hits = len(entries) - use.misses
    return parsed


def parse_tree(tree: SourceTree, *, max_source_files: int, max_file_bytes: int) -> ParseOutput:
    """Inventory and parse every file of ``tree`` in path order."""
    inventory = build_inventory(
        tree, max_source_files=max_source_files, max_file_bytes=max_file_bytes
    )
    files = parse_files(tree, inventory)
    stats = ParseStats(
        files_parsed=len(files),
        files_with_issues=sum(1 for parsed in files if parsed.issue_count),
        symbols=sum(len(parsed.symbols) for parsed in files),
        imports=sum(len(parsed.imports) for parsed in files),
        calls=sum(len(parsed.calls) for parsed in files),
        tests=sum(len(parsed.tests) for parsed in files),
        routes=sum(len(parsed.routes) for parsed in files),
    )
    body = {
        "source": tree.label,
        "commit": tree.commit,
        "inventory": inventory.model_dump(mode="json"),
        "files": [parsed.model_dump(mode="json") for parsed in files],
        "stats": stats.model_dump(mode="json"),
    }
    return ParseOutput(
        source=tree.label,
        commit=tree.commit,
        inventory=inventory,
        files=files,
        stats=stats,
        digest=digest(body),
    )


def run_parse(
    target: str, settings: Settings, *, commit: str | None = None, keep_workdir: bool = False
) -> ParseOutput:
    """Parse a local directory or a public GitHub repository (``https://github.com/o/r``)."""
    caps = {
        "max_source_files": settings.max_source_files,
        "max_file_bytes": settings.max_file_bytes,
    }
    local = Path(target)
    if local.is_dir():
        with DirectorySourceTree(local, label=f"dir:{local.resolve().name}") as tree:
            return parse_tree(tree, **caps)

    ref = parse_repo_url(target)
    with GitHubClient.from_settings(settings) as github:
        meta = github.get_repo(ref)
        check_repo_size(meta, settings.max_repo_size_kb)
        sha = commit or github.resolve_head(meta.ref, meta.default_branch)
    with (
        Workspace(settings.data_dir / "work", keep=keep_workdir) as workspace,
        fetch_source(
            meta.ref.clone_url,
            sha,
            workspace.path / "source.git",
            label=f"github.com/{meta.ref.full_name}",
            timeout=settings.git_timeout_s,
        ) as tree,
    ):
        return parse_tree(tree, **caps)
