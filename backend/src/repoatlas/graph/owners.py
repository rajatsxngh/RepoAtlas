"""File owners from commit history (GRA-9).

``read_commit_log`` reads the last N commits from the blobless history clone with
``git log --no-renames --name-only`` (no blame, numstat, or rename detection: each would download
file contents). ``compute_owners`` is pure: per inventoried path, each author's commit count,
last commit time, and share of the path's commits.

Authors are identified by a hash of their email (sha256 of the trimmed, lowercased address,
truncated); the email itself is never stored. Merge commits list no files. Commits at the
shallow boundary are kept but list no files either: without their parents, git would report
every file in the tree as changed by them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from repoatlas.graph.model import Owner, owner_sort_key
from repoatlas.ingest.fetch import HistoryStore

DEFAULT_OWNER_COMMITS = 1000
AUTHOR_HASH_HEX = 16
_RECORD, _FIELD = "\x1e", "\x1f"
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


class CommitRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha: str
    author_name: str
    author_hash: str
    committed_at: datetime  # the author date, UTC
    paths: tuple[str, ...] = ()


class CommitLog(BaseModel):
    """Commits, most recent first, as ``git log`` lists them."""

    model_config = ConfigDict(frozen=True)

    commits: tuple[CommitRecord, ...] = ()


def author_hash(email: str) -> str:
    """The stored identity of an author: never the email itself."""
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return digest[:AUTHOR_HASH_HEX]


def read_commit_log(store: HistoryStore, limit: int = DEFAULT_OWNER_COMMITS) -> CommitLog:
    """The ``limit`` most recent commits behind the store's commit, with the paths each touched."""
    if limit < 1:
        return CommitLog()
    output = store.git(
        "-c",
        "core.quotePath=false",
        "log",
        "--no-renames",
        "--name-only",
        f"--format={_RECORD}%H{_FIELD}%an{_FIELD}%ae{_FIELD}%at",
        "-n",
        str(limit),
        store.commit,
    )
    return parse_commit_log(output, shallow=_shallow_commits(store.path))


def parse_commit_log(output: str, *, shallow: Iterable[str] = ()) -> CommitLog:
    """Parse ``git log`` output in ``read_commit_log``'s format."""
    boundary = set(shallow)
    commits: list[CommitRecord] = []
    for record in output.split(_RECORD):
        if not record.strip():
            continue
        header, _, body = record.partition("\n")
        fields = header.split(_FIELD)
        if len(fields) != 4 or not fields[3].strip().isdigit():
            continue
        sha, name, email, timestamp = fields
        paths = () if sha in boundary else _paths(body)
        commits.append(
            CommitRecord(
                sha=sha,
                author_name=name,
                author_hash=author_hash(email),
                committed_at=datetime.fromtimestamp(int(timestamp), tz=UTC),
                paths=paths,
            )
        )
    return CommitLog(commits=tuple(commits))


def compute_owners(
    log: CommitLog, paths: Iterable[str], limit: int = DEFAULT_OWNER_COMMITS
) -> dict[str, list[Owner]]:
    """Owners of each path in ``paths`` from the ``limit`` most recent commits of ``log``."""
    wanted = set(paths)
    tallies: dict[str, dict[str, _Tally]] = {}
    for commit in log.commits[:limit]:
        for path in dict.fromkeys(commit.paths):
            if path not in wanted:
                continue
            authors = tallies.setdefault(path, {})
            tally = authors.get(commit.author_hash)
            if tally is None:  # the log is newest first: the first sighting is the latest
                authors[commit.author_hash] = _Tally(commit.author_name, 1, commit.committed_at)
            else:
                tally.commits += 1
                tally.last = max(tally.last, commit.committed_at)
    owners: dict[str, list[Owner]] = {}
    for path, authors in tallies.items():
        total = sum(tally.commits for tally in authors.values())
        found = [
            Owner(
                author_name=tally.name,
                author_hash=identity,
                commits=tally.commits,
                last_commit_at=tally.last,
                share=tally.commits / total,
            )
            for identity, tally in authors.items()
        ]
        owners[path] = sorted(found, key=owner_sort_key)  # most commits, then most recent
    return owners


@dataclass
class _Tally:
    name: str  # as of the author's latest commit
    commits: int
    last: datetime


def _paths(body: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_unquote(line) for line in body.split("\n") if line.strip()))


def _unquote(line: str) -> str:
    """Undo git's C-style quoting of unusual paths (``"a\\tb.py"``)."""
    if len(line) < 2 or not (line.startswith('"') and line.endswith('"')):
        return line
    raw, data, i = line[1:-1], bytearray(), 0
    while i < len(raw):
        char = raw[i]
        if char != "\\" or i + 1 >= len(raw):
            data += char.encode("utf-8")
            i += 1
        elif raw[i + 1] in _ESCAPES:
            data.append(_ESCAPES[raw[i + 1]])
            i += 2
        elif raw[i + 1 : i + 4].isdigit() and len(raw[i + 1 : i + 4]) == 3:
            data.append(int(raw[i + 1 : i + 4], 8) & 0xFF)
            i += 4
        else:
            data += char.encode("utf-8")
            i += 1
    return data.decode("utf-8", "replace")


def _shallow_commits(git_dir: Path) -> set[str]:
    try:
        return set((git_dir / "shallow").read_text(encoding="ascii").split())
    except OSError:
        return set()
