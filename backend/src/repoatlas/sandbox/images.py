"""Base images and per-repo images (SBX-2, SBX-5).

A repo image is built in three steps, none of which mounts anything from the host:

1. **Source layer.** The commit's files come from ``fetch_source`` (git objects, no working
   tree) and are streamed to ``docker build`` as a tar context with ``--network none``.
2. **Install.** A container from the source layer runs the recipe's install commands on the
   internal network, whose only exit is the allowlist proxy. It runs as uid 10001 with all
   capabilities dropped.
3. **Commit.** The install container becomes the repo image. Its digest is recorded.

Base images are built from our own Dockerfiles in ``sandbox/images/`` (pinned inputs, no repo
code), so they may use the default network.
"""

from __future__ import annotations

import hashlib
import io
import shlex
import tarfile
import time
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from repoatlas.config import get_settings
from repoatlas.ingest.fetch import fetch_source, remove_tree
from repoatlas.ingest.source import GitSourceTree, SourceTree, TreeEntry
from repoatlas.sandbox.docker import DockerError, docker, image_id, remove_container, remove_image
from repoatlas.sandbox.network import INTERNAL_NETWORK, ensure_proxy, proxy_env
from repoatlas.sandbox.paths import sandbox_dir
from repoatlas.sandbox.recipe import Recipe

SANDBOX_UID = 10001
IMAGE_PREFIX = "repoatlas-sbx"
INSTALL_TIMEOUT_S = 3600
SYMLINK_MODE = "120000"
# A fixed timestamp for every source file, so contexts are byte-identical across builds.
# Not 0: wheel builders reject dates before 1980.
SOURCE_MTIME = 946_684_800  # 2000-01-01T00:00:00Z


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ImageInfo(_Model):
    tag: str
    digest: str  # the local image ID, sha256:…


class BaseImageInfo(ImageInfo):
    name: str
    dockerfile_sha256: str
    upstream: list[str]  # FROM references, each pinned by digest


class RepoImageInfo(_Model):
    repo_id: str
    commit: str
    recipe_sha256: str
    base: BaseImageInfo
    source: ImageInfo
    image: ImageInfo
    files: int
    install_seconds: float


# --------------------------------------------------------------------------------------------
# Base images
# --------------------------------------------------------------------------------------------


def _dockerfile(name: str) -> Path:
    return sandbox_dir() / "images" / f"{name}.Dockerfile"


def base_image_tag(name: str) -> str:
    """Content-addressed tag: it changes whenever the Dockerfile or a pinned input does."""
    images = sandbox_dir() / "images"
    digest = hashlib.sha256(_dockerfile(name).read_bytes())
    tool_dir = images / ("scip-python" if name == "python" else "scip-typescript")
    for path in sorted(tool_dir.glob("package*.json")):
        digest.update(path.read_bytes())
    return f"{IMAGE_PREFIX}/base-{name}:{digest.hexdigest()[:12]}"


def upstream_refs(dockerfile_text: str) -> list[str]:
    refs = []
    for line in dockerfile_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper() == "FROM":
            refs.append(parts[1])
    return refs


def ensure_base_image(name: str) -> BaseImageInfo:
    """Build ``sandbox/images/<name>.Dockerfile`` unless the tag already exists."""
    dockerfile = _dockerfile(name)
    if not dockerfile.is_file():
        raise ValueError(f"No base image named {name!r}")
    tag = base_image_tag(name)
    if image_id(tag) is None:
        docker(
            ["build", "-q", "-f", str(dockerfile), "-t", tag, str(dockerfile.parent)],
            timeout=INSTALL_TIMEOUT_S,
        )
    found = image_id(tag)
    assert found is not None
    text = dockerfile.read_text(encoding="utf-8")
    return BaseImageInfo(
        name=name,
        tag=tag,
        digest=found,
        dockerfile_sha256=hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
        upstream=upstream_refs(text),
    )


# --------------------------------------------------------------------------------------------
# Source layer
# --------------------------------------------------------------------------------------------


def _tar_info(name: str, size: int, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.mtime = SOURCE_MTIME
    info.uid = info.gid = SANDBOX_UID
    info.uname = info.gname = "sandbox"
    return info


def source_context(
    tree: SourceTree, dockerfile: str, *, overrides: dict[str, bytes] | None = None
) -> tuple[bytes, int]:
    """A tar build context: ``Dockerfile`` plus the tree's files under ``src/``.

    Regular files keep their executable bit, symlinks stay symlinks (Docker resolves them
    inside the image, never on the host), and submodules are skipped. ``overrides`` replaces the
    contents of named files (used for mutated sources). Returns the tar bytes and file count.
    """
    buffer = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        data = dockerfile.encode("utf-8")
        tar.addfile(_tar_info("Dockerfile", len(data), 0o644), io.BytesIO(data))
        for entry in _source_entries(tree):
            name = "src/" + entry.path
            if entry.mode == SYMLINK_MODE:
                info = _tar_info(name, 0, 0o777)
                info.type = tarfile.SYMTYPE
                info.linkname = tree.read(entry).decode("utf-8", "replace")
                tar.addfile(info)
            else:
                content = tree.read(entry)
                if overrides and entry.path in overrides:
                    content = overrides[entry.path]
                mode = 0o755 if entry.mode == "100755" else 0o644
                tar.addfile(_tar_info(name, len(content), mode), io.BytesIO(content))
            count += 1
    return buffer.getvalue(), count


def _source_entries(tree: SourceTree) -> Iterable[TreeEntry]:
    for entry in tree.entries():
        if entry.is_regular or entry.mode == SYMLINK_MODE:
            yield entry


def build_from_context(context: bytes, tag: str) -> ImageInfo:
    # --no-cache: BuildKit keys COPY on content only, so a cached layer could keep old mtimes.
    docker(
        ["build", "-q", "--no-cache", "--network", "none", "-t", tag, "-"],
        input=context,
        timeout=1800,
    )
    found = image_id(tag)
    assert found is not None
    return ImageInfo(tag=tag, digest=found)


def source_cache_dir(recipe: Recipe) -> Path:
    return get_settings().data_dir / "sandbox" / "git" / f"{recipe.id}-{recipe.commit[:12]}"


def open_source(recipe: Recipe) -> SourceTree:
    """The recipe's commit as a ``SourceTree`` (fetched once, then read from the local cache)."""
    dest = source_cache_dir(recipe)
    label = f"{recipe.id}@{recipe.commit[:12]}"
    if (dest / "HEAD").is_file():
        return GitSourceTree(dest, recipe.commit, label=label)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        remove_tree(partial)
    tree = fetch_source(recipe.clone_url, recipe.commit, partial, label=label)
    tree.close()
    partial.rename(dest)
    return GitSourceTree(dest, recipe.commit, label=label)


def repo_tag(recipe: Recipe, stage: str = "") -> str:
    suffix = f"-{stage}" if stage else ""
    return f"{IMAGE_PREFIX}/{recipe.id}{suffix}:{recipe.commit[:12]}"


# --------------------------------------------------------------------------------------------
# Install and commit
# --------------------------------------------------------------------------------------------


def install_script(recipe: Recipe) -> str:
    """The shell script the install container runs. Proxy settings are exported inside the
    script, not passed with ``-e``, so they are not recorded in the committed image."""
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(proxy_env().items()))
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(recipe.env.items()))
    lines = ["set -eu", f"export {exports}", "cd /src"]
    if env:
        lines.append(f"export {env}")
    lines.extend(recipe.install)
    return "\n".join(lines)


def install_run_args(container: str, image: str) -> list[str]:
    """``docker run`` flags for the install container: internal network, no privileges."""
    return [
        "run",
        "--name",
        container,
        "--network",
        INTERNAL_NETWORK,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"{SANDBOX_UID}:{SANDBOX_UID}",
        "--pids-limit",
        "1024",
        "--memory",
        "4g",
        "--cpus",
        "2",
        image,
    ]


def build_image(recipe: Recipe, *, tree: SourceTree | None = None) -> RepoImageInfo:
    """Build (or rebuild) the repo image for ``recipe``; see the module docstring.

    ``tree`` overrides where the source comes from (tests pass a fixture directory).
    """
    base = ensure_base_image(recipe.base_image)
    ensure_proxy()
    dockerfile = f"FROM {base.tag}\nCOPY --chown={SANDBOX_UID}:{SANDBOX_UID} src/ /src/\n"
    with tree or open_source(recipe) as opened:
        context, files = source_context(opened, dockerfile)
    source = build_from_context(context, repo_tag(recipe, "src"))

    container = f"{IMAGE_PREFIX}-install-{recipe.id}"
    remove_container(container)
    started = time.monotonic()
    try:
        result = docker(
            [*install_run_args(container, source.tag), "sh", "-c", install_script(recipe)],
            timeout=INSTALL_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            log = (result.stdout + result.stderr).decode("utf-8", "replace").splitlines()
            raise DockerError(
                f"Install for {recipe.id} failed (exit {result.returncode}):\n"
                + "\n".join(log[-40:])
            )
        elapsed = time.monotonic() - started
        tag = repo_tag(recipe)
        remove_image(tag)
        docker(
            [
                "commit",
                "--change",
                f"USER {SANDBOX_UID}:{SANDBOX_UID}",
                "--change",
                "WORKDIR /src",
                "--change",
                'CMD ["sh"]',
                container,
                tag,
            ]
        )
    finally:
        remove_container(container)
    found = image_id(tag)
    assert found is not None
    return RepoImageInfo(
        repo_id=recipe.id,
        commit=recipe.commit,
        recipe_sha256=recipe.digest(),
        base=base,
        source=source,
        image=ImageInfo(tag=tag, digest=found),
        files=files,
        install_seconds=round(elapsed, 1),
    )
