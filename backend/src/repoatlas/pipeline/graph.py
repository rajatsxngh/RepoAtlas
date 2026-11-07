"""Build the code graph of a local directory or a public GitHub repository (GRA-11).

Fetch (or read) → inventory → parse → project files → history → ``build_graph``. Local
directories have no history, so their graphs have no owners.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from repoatlas.config import Settings
from repoatlas.graph.build import GraphSettings, build_graph
from repoatlas.graph.keys import parent_dir
from repoatlas.graph.model import CodeGraph
from repoatlas.graph.owners import CommitLog, read_commit_log
from repoatlas.graph.project import ProjectFiles, join, load_jsonc
from repoatlas.ingest.fetch import Workspace, fetch_history, fetch_source
from repoatlas.ingest.github import GitHubClient, check_repo_size, parse_repo_url
from repoatlas.ingest.source import DirectorySourceTree, SourceTree, TreeEntry
from repoatlas.pipeline.parse import parse_tree

PROJECT_FILE_NAMES = frozenset({"pyproject.toml", "package.json"})
_CONFIG_NAME = re.compile(r"[tj]sconfig(?:\.[^/]+)?\.json")
_IGNORED_FOLDERS = frozenset(
    {"node_modules", "bower_components", "site-packages", ".venv", "venv", "__pycache__", ".tox"}
)


class GraphOutput(BaseModel):
    """Everything ``repoatlas graph`` produces. ``digest`` is the graph's digest."""

    model_config = ConfigDict(frozen=True)

    source: str
    commit: str | None
    parse_digest: str
    graph: CodeGraph
    digest: str


def read_project_files(tree: SourceTree, *, max_file_bytes: int) -> ProjectFiles:
    """``pyproject.toml``, ``package.json``, and ``tsconfig*.json``/``jsconfig*.json`` files
    (outside dependency folders), plus the configs those extend by relative path."""
    entries = {entry.path: entry for entry in tree.entries() if entry.is_regular}
    files: dict[str, str] = {}
    for path, entry in entries.items():
        name = path.rsplit("/", 1)[-1]
        folders = path.split("/")[:-1]
        wanted = name in PROJECT_FILE_NAMES or _CONFIG_NAME.fullmatch(name) is not None
        if wanted and not _IGNORED_FOLDERS.intersection(folders):
            text = _read_text(tree, entry, max_file_bytes)
            if text is not None:
                files[path] = text
    for path in [p for p in files if _CONFIG_NAME.fullmatch(p.rsplit("/", 1)[-1])]:
        data = load_jsonc(files[path])
        extends = data.get("extends") if isinstance(data, dict) else None
        folder = "" if parent_dir(path) == "." else parent_dir(path)
        for extended in extends if isinstance(extends, list) else [extends]:
            if not isinstance(extended, str) or not extended.startswith("."):
                continue
            target = join(folder, extended)
            for candidate in (target, f"{target}.json"):
                if candidate in entries and candidate not in files:
                    text = _read_text(tree, entries[candidate], max_file_bytes)
                    if text is not None:
                        files[candidate] = text
    return ProjectFiles(files=dict(sorted(files.items())))


def _read_text(tree: SourceTree, entry: TreeEntry, max_file_bytes: int) -> str | None:
    if entry.size is not None and entry.size > max_file_bytes:
        return None
    try:
        return tree.read(entry).decode("utf-8")
    except UnicodeDecodeError:
        return None


def graph_tree(
    tree: SourceTree,
    *,
    max_source_files: int,
    max_file_bytes: int,
    history: CommitLog | None = None,
    settings: GraphSettings | None = None,
) -> GraphOutput:
    """Inventory, parse, and build the graph of ``tree``."""
    parsed = parse_tree(tree, max_source_files=max_source_files, max_file_bytes=max_file_bytes)
    project = read_project_files(tree, max_file_bytes=max_file_bytes)
    graph = build_graph(parsed.inventory, parsed.files, history, settings, project=project)
    return GraphOutput(
        source=tree.label,
        commit=tree.commit,
        parse_digest=parsed.digest,
        graph=graph,
        digest=graph.digest(),
    )


def run_graph(
    target: str,
    settings: Settings,
    *,
    commit: str | None = None,
    keep_workdir: bool = False,
    with_history: bool = True,
) -> GraphOutput:
    """Build the graph of a local directory or a public GitHub repository."""
    graph_settings = GraphSettings.from_settings(settings)
    caps = settings.max_source_files, settings.max_file_bytes
    local = Path(target)
    if local.is_dir():
        with DirectorySourceTree(local, label=f"dir:{local.resolve().name}") as tree:
            return graph_tree(
                tree, max_source_files=caps[0], max_file_bytes=caps[1], settings=graph_settings
            )

    ref = parse_repo_url(target)
    with GitHubClient.from_settings(settings) as github:
        meta = github.get_repo(ref)
        check_repo_size(meta, settings.max_repo_size_kb)
        sha = commit or github.resolve_head(meta.ref, meta.default_branch)
    with Workspace(settings.data_dir / "work", keep=keep_workdir) as workspace:
        history: CommitLog | None = None
        if with_history:
            store = fetch_history(
                meta.ref.clone_url,
                sha,
                workspace.path / "history.git",
                depth=settings.history_depth,
                timeout=settings.git_timeout_s,
            )
            history = read_commit_log(store, settings.owner_commits)
        with fetch_source(
            meta.ref.clone_url,
            sha,
            workspace.path / "source.git",
            label=f"github.com/{meta.ref.full_name}",
            timeout=settings.git_timeout_s,
        ) as tree:
            return graph_tree(
                tree,
                max_source_files=caps[0],
                max_file_bytes=caps[1],
                history=history,
                settings=graph_settings,
            )
