"""File ownership from commit history (BEN-5), independent of RepoAtlas's graph.

A file is *mostly changed by* an author when that author made more than half of the non-merge
commits that touched the file, counting every commit up to the pinned commit, with at least
``MIN_COMMITS`` such commits. Authors are display names (``%an``) only; emails are never read.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

MIN_COMMITS = 3
_COMMIT_MARK = "\x1ecommit\x1f"


class Ownership(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commits: int  # non-merge commits read
    owners: dict[str, str]  # path → author who made most of its commits
    file_commits: dict[str, int]  # path → commits that touched it


def parse_log(text: str, paths: Iterable[str]) -> Ownership:
    """Ownership from ``git log --no-merges --no-renames --name-only --format=<mark>%an``."""
    keep = set(paths)
    by_file: dict[str, Counter[str]] = defaultdict(Counter)
    commits = 0
    for block in text.split(_COMMIT_MARK)[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        commits += 1
        author = lines[0].strip()
        for path in {line.strip() for line in lines[1:] if line.strip()}:
            if path in keep:
                by_file[path][author] += 1
    owners: dict[str, str] = {}
    counts: dict[str, int] = {}
    for path in sorted(by_file):
        total = sum(by_file[path].values())
        counts[path] = total
        author, top = min(by_file[path].items(), key=lambda kv: (-kv[1], kv[0]))
        if total >= MIN_COMMITS and top * 2 > total:
            owners[path] = author
    return Ownership(commits=commits, owners=owners, file_commits=counts)


def log_args(commit: str) -> list[str]:
    return [
        "log",
        "--no-merges",
        "--no-renames",
        "--name-only",
        f"--format={_COMMIT_MARK}%an",
        commit,
    ]


def history_ownership(clone_url: str, commit: str, cache: Path, paths: Iterable[str]) -> Ownership:
    """Fetch the full history behind ``commit`` (blobless, cached) and compute ownership."""
    from repoatlas.ingest.fetch import HistoryStore, fetch_history, remove_tree

    if not (cache / "HEAD").is_file():
        partial = cache.with_name(cache.name + ".partial")
        if partial.exists():
            remove_tree(partial)
        cache.parent.mkdir(parents=True, exist_ok=True)
        fetch_history(clone_url, commit, partial, depth=1_000_000, timeout=900).path.rename(cache)
    store = HistoryStore(path=cache, commit=commit, timeout=900)
    return parse_log(store.git(*log_args(commit)), paths)
