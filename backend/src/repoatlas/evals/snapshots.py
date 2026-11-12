"""Which snapshot each benchmark repo is evaluated on.

A question names its repo and commit; it is answered on the newest ready snapshot of that repo
and commit built with the current pipeline configuration (the same config fingerprint a new
build would get), so scores always describe the code as it is. ``--snapshot repo=ID`` picks one
by hand (any fingerprint). A repo without a snapshot is reported with the command that builds
it.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

from sqlalchemy import Engine, text

from repoatlas.evals.report import SnapshotUsed


class SnapshotMissing(LookupError):
    pass


def parse_overrides(values: Iterable[str]) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for value in values:
        repo, sep, number = value.partition("=")
        if not sep or not number.isdigit():
            raise ValueError(f"--snapshot takes repo=ID, not {value!r}.")
        overrides[repo.strip()] = int(number)
    return overrides


def _owner_name(url: str) -> tuple[str, str]:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0], parts[1].removesuffix(".git")


def resolve_snapshots(
    engine: Engine,
    repos: dict[str, str],  # repo ID → commit
    *,
    fingerprint: str,
    overrides: dict[str, int] | None = None,
) -> dict[str, SnapshotUsed]:
    from repoatlas.bench.repos import recipes

    known = recipes()
    found: dict[str, SnapshotUsed] = {}
    missing: list[str] = []
    with engine.connect() as connection:
        for repo_id, commit in sorted(repos.items()):
            override = (overrides or {}).get(repo_id)
            if override is not None:
                row = connection.execute(
                    text(
                        "SELECT id, commit_sha, config_fingerprint, embedding_model, status "
                        "FROM snapshots WHERE id = :id"
                    ),
                    {"id": override},
                ).first()
                if row is None or row.status != "ready":
                    raise SnapshotMissing(f"Snapshot {override} ({repo_id}) is not ready.")
                if row.commit_sha != commit:
                    raise SnapshotMissing(
                        f"Snapshot {override} is at {row.commit_sha[:12]}, not {commit[:12]}."
                    )
            else:
                recipe = known.get(repo_id)
                if recipe is None:
                    raise SnapshotMissing(f"No sandbox recipe for repo {repo_id!r}.")
                owner, name = _owner_name(recipe.url)
                row = connection.execute(
                    text(
                        "SELECT s.id, s.commit_sha, s.config_fingerprint, s.embedding_model, "
                        "s.status FROM snapshots s JOIN repos r ON r.id = s.repo_id "
                        "WHERE lower(r.owner) = lower(:owner) AND lower(r.name) = lower(:name) "
                        "AND s.commit_sha = :commit AND s.status = 'ready' "
                        "AND s.config_fingerprint = :fp ORDER BY s.id DESC LIMIT 1"
                    ),
                    {"owner": owner, "name": name, "commit": commit, "fp": fingerprint},
                ).first()
                if row is None:
                    missing.append(f"repoatlas build {recipe.url} --commit {commit}")
                    continue
            found[repo_id] = SnapshotUsed(
                repo_id=repo_id,
                snapshot_id=row.id,
                commit=row.commit_sha,
                config_fingerprint=row.config_fingerprint,
                embedding_model=row.embedding_model,
            )
    if missing:
        raise SnapshotMissing(
            "No ready snapshot with the current configuration for:\n  " + "\n  ".join(missing)
        )
    return found
