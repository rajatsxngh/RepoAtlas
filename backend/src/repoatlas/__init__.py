"""RepoAtlas: interactive code maps with cited answers and validated impact analysis."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("repoatlas")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
