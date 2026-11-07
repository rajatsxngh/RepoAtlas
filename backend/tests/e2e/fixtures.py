"""Fixture repositories served as https://github.com/repoatlas-e2e/<name>, without the network.

- ``shop``: the backend's ``py_small`` fixture repository (packages, classes, routes, tests),
  committed by three authors so files have owners.
- ``xss``: HTML and script payloads everywhere repo text reaches the web app: folder and file
  names, comments, docstrings, strings, test titles, a route pattern, and a commit author.
- ``private``: GitHub answers that it is private.

``github_transport`` answers the GitHub API calls the ingestion makes; ``patch_fetches`` points
the build's git fetches at the local bare repositories.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from repoatlas.ingest import fetch as fetch_module
from repoatlas.ingest.git import run_git
from repoatlas.pipeline import build as build_module
from repoatlas.pipeline import graph as graph_module
from tests.unit.ingest.gitrepo import OriginRepo, Spec

OWNER = "repoatlas-e2e"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PY_SMALL = FIXTURES / "repos" / "py_small"

XSS_DIR = "<img src=x onerror=alert('dir')>"
XSS_FILE = "<svg onload=alert('file')>.py"
XSS_TS_FILE = "\"><svg onload=alert('svg')>.ts"
XSS_TEST_FILE = "<svg onload=alert('spec')>.test.ts"
XSS_OWNER = "&lt;img src=x onerror=alert('owner')&gt; & \"Ra\" Lima"
XSS_ROUTE = "/<script>alert('route')</script>"

XSS_PY = f"""# <script>alert('comment')</script>
\"\"\"<b onmouseover=alert('docstring')>hover</b>\"\"\"

from fastapi import FastAPI

app = FastAPI()


@app.get("{XSS_ROUTE}")
def render(name="<iframe src=javascript:alert('default')>"):
    \"\"\"Returns <a href="javascript:alert('link')">a link</a>.\"\"\"
    return helper("</textarea><script>alert('string')</script>")


def helper(text):
    # &lt;not an entity&gt; &amp; <!-- comment -->
    return text
""".encode()

XSS_TS = b"""// <script>alert('comment')</script>
/** <b onmouseover=alert('docstring')>hover</b> */
export function show(value: string): string {
  return `<div onclick="alert('template')">${value}</div>`;
}
"""

XSS_TEST = b"""function show(value: string): string {
  return value;
}

describe("<img src=x onerror=alert('describe')>", () => {
  it("<script>alert('test')</script> renders", () => {
    show("<script>alert('test body')</script>");
  });
});
"""


def commit_as(repo: OriginRepo, files: list[Spec], author: str, message: str) -> str:
    """A commit with the given tree and author on top of ``repo``'s head (``main``)."""
    tree = repo._tree(files)
    # Owners are keyed by (hashed) email, so each author needs their own.
    email = "".join(char for char in author.lower() if char.isalnum()) or "author"
    args = ["-c", f"user.name={author}", "-c", f"user.email={email}@example.invalid"]
    args += ["commit-tree", tree, "-m", message]
    if repo.head:
        args += ["-p", repo.head]
    repo.head = run_git(args, cwd=repo.path).decode().strip()
    run_git(["update-ref", "refs/heads/main", repo.head], cwd=repo.path)
    return repo.head


def _py_small_files() -> list[Spec]:
    files = []
    for path in sorted(PY_SMALL.rglob("*")):
        if path.is_file():
            files.append(Spec(path.relative_to(PY_SMALL).as_posix(), path.read_bytes()))
    return files


def build_shop(path: Path) -> OriginRepo:
    repo = OriginRepo(path)
    files = _py_small_files()
    cart = next(spec for spec in files if spec.path == "src/shop/cart.py")
    others = [spec for spec in files if spec.path != cart.path]

    def with_cart(data: bytes) -> list[Spec]:
        return [*others, Spec(cart.path, data)]

    # Two authors change the cart, so it has owners with different shares.
    commit_as(repo, files, "Avery Chen", "Start the shop")
    commit_as(repo, with_cart(cart.data + b"\n# Totals round to cents.\n"), "Sam Okafor", "Note")
    commit_as(
        repo, with_cart(cart.data + b"\n# Totals round to cents (half up).\n"), "Avery Chen", "Fix"
    )
    return repo


def build_xss(path: Path) -> OriginRepo:
    repo = OriginRepo(path)
    files = [
        Spec(f"{XSS_DIR}/{XSS_FILE}", XSS_PY),
        Spec(XSS_TS_FILE, XSS_TS),
        Spec(XSS_TEST_FILE, XSS_TEST),
        Spec("pyproject.toml", b"[project]\nname = 'xss'\n"),
    ]
    commit_as(repo, files, XSS_OWNER, "Add payloads")
    return repo


def build_repos(root: Path) -> dict[str, OriginRepo]:
    root.mkdir(parents=True, exist_ok=True)
    return {"shop": build_shop(root / "shop.git"), "xss": build_xss(root / "xss.git")}


def _repo_json(name: str, *, private: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "github" / "repo_pallets_flask.json").read_text("utf-8")
    )
    data.update(
        name=name,
        full_name=f"{OWNER}/{name}",
        private=private,
        html_url=f"https://github.com/{OWNER}/{name}",
        size=120,
        default_branch="main",
    )
    data["owner"] = {"login": OWNER, "type": "Organization"}
    return data


def github_transport(repos: dict[str, OriginRepo]) -> httpx.MockTransport:
    """The GitHub API as the ingestion sees it, for the fixture repositories only."""

    def answer(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "repos" or parts[1] != OWNER:
            return httpx.Response(404, json={"message": "Not Found"})
        name = parts[2]
        if name == "private":
            return httpx.Response(200, json=_repo_json(name, private=True))
        repo = repos.get(name)
        if repo is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if len(parts) >= 5 and parts[3] == "commits":
            return httpx.Response(200, text=repo.head or "")
        return httpx.Response(200, json=_repo_json(name))

    return httpx.MockTransport(answer)


def patch_fetches(repos: dict[str, OriginRepo], setattr_: Any) -> None:
    """Point the build's fetches of https://github.com/repoatlas-e2e/<name>.git at the local
    bare repositories. ``setattr_`` is ``monkeypatch.setattr`` or the builtin ``setattr``."""
    real_source, real_history = fetch_module.fetch_source, fetch_module.fetch_history
    prefix = f"https://github.com/{OWNER}/"

    def local(clone_url: str) -> str:
        if not clone_url.startswith(prefix):
            raise ValueError(f"Only {prefix} repositories are served in end-to-end runs.")
        name = clone_url.removeprefix(prefix).removesuffix(".git")
        return repos[name].url

    def source(clone_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        return real_source(local(clone_url), commit, dest, **kwargs)

    def history(clone_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        return real_history(local(clone_url), commit, dest, **kwargs)

    for module in (build_module, graph_module):
        setattr_(module, "fetch_source", source)
        setattr_(module, "fetch_history", history)
