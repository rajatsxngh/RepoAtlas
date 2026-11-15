#!/usr/bin/env python3
"""Check the links of the README and the docs.

    python scripts/check_links.py                 # relative links and heading anchors
    python scripts/check_links.py --external      # also request every http(s) link (network)
    python scripts/check_links.py README.md docs/results.md

Checked in every Markdown file given (default: the Markdown files at the repository root and
under ``docs/`` and ``launch/``):

- a relative link or image points at a file or folder that exists inside the repository;
- a ``#fragment`` on a Markdown target (or on the file itself) names one of its headings, by
  GitHub's slug rules;
- with ``--external``, every ``http://`` or ``https://`` link answers. A site that refuses
  automated clients (401, 403, 429) is reported as unverified, not as broken.

Links inside code spans and fenced code blocks are not links and are skipped. Exits with 1
when a link is broken. Uses only the standard library, so CI needs nothing installed.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOBS = ("*.md", "docs/**/*.md", "launch/**/*.md")
USER_AGENT = "link-check (python-urllib)"
UNVERIFIABLE = frozenset({401, 403, 429})

_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
# [text](target "title") and ![alt](target); the target has no spaces unless in <...>.
_INLINE = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HTML_ANCHOR = re.compile(r"<a\s+(?:name|id)=\"([^\"]+)\"")
_MD_LINK_TEXT = re.compile(r"!?\[((?:[^\]\\]|\\.)*)\]\([^)]*\)")


@dataclass(frozen=True)
class Link:
    source: Path
    line: int
    target: str


@dataclass(frozen=True)
class Problem:
    link: Link
    message: str

    def render(self, root: Path) -> str:
        try:
            where = self.link.source.relative_to(root).as_posix()
        except ValueError:
            where = self.link.source.as_posix()
        return f"{where}:{self.link.line}: {self.link.target}: {self.message}"


def prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """The lines of ``text`` outside fenced code blocks, with code spans blanked out."""
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        opened = _FENCE.match(line)
        if fence is None and opened:
            fence = opened.group(1)[0] * 3
            continue
        if fence is not None:
            if line.strip().startswith(fence) and not line.strip().strip(fence[0]):
                fence = None
            continue
        yield number, _CODE_SPAN.sub(lambda found: " " * len(found.group(0)), line)


def find_links(source: Path, text: str) -> list[Link]:
    links: list[Link] = []
    for number, line in prose_lines(text):
        targets = [found.group(1) for found in _INLINE.finditer(line)]
        targets += [found.group(1) for found in _AUTOLINK.finditer(line)]
        reference = _REFERENCE.match(line)
        if reference:
            targets.append(reference.group(1))
        for target in targets:
            links.append(Link(source, number, target.strip("<>")))
    return links


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: formatting dropped, lowercased, punctuation removed,
    spaces to hyphens."""
    text = _MD_LINK_TEXT.sub(r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("*", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every anchor a Markdown document defines: heading slugs (repeats get ``-1``, ``-2``)
    and explicit ``<a name>`` / ``<a id>`` anchors."""
    found: set[str] = set(_HTML_ANCHOR.findall(text))
    seen: dict[str, int] = {}
    fence: str | None = None
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if opened:
            marker = opened.group(1)[0] * 3
            fence = None if fence == marker else (fence or marker)
            continue
        if fence is not None:
            continue
        heading = _HEADING.match(line)
        if not heading:
            continue
        base = slug(heading.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def check_local(link: Link, root: Path) -> str | None:
    """Why a relative link is broken, or ``None``."""
    parts = urlsplit(link.target)
    path_text = unquote(parts.path)
    if path_text.startswith("/"):
        return "use a relative path (a path starting with / means something else on GitHub)"
    target = (link.source.parent / path_text).resolve() if path_text else link.source.resolve()
    if root.resolve() not in (target, *target.parents):
        return "points outside the repository"
    if not target.exists():
        return "no such file"
    wanted = unquote(parts.fragment).lower()
    if wanted and target.is_file() and target.suffix.lower() == ".md":
        return (
            None
            if wanted in anchors(target.read_text(encoding="utf-8"))
            else f"no heading for #{parts.fragment}"
        )
    return None


def fetch_status(url: str, timeout: float = 20.0) -> int:
    """The HTTP status of ``url`` (HEAD, then GET where HEAD is not allowed); 0 when no
    response arrived."""
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(
            url, method=method, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status)
        except urllib.error.HTTPError as error:
            if method == "HEAD" and error.code in {400, 403, 404, 405, 501}:
                continue
            return int(error.code)
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "GET":
                return 0
    return 0


def check_files(
    files: Iterable[Path],
    root: Path,
    *,
    external: bool = False,
    status_of: Callable[[str], int] = fetch_status,
) -> tuple[list[Problem], list[Problem], int]:
    """Broken links, links that could not be verified, and how many links were checked."""
    broken: list[Problem] = []
    unverified: list[Problem] = []
    checked = 0
    statuses: dict[str, int] = {}
    for source in files:
        for link in find_links(source, source.read_text(encoding="utf-8")):
            scheme = urlsplit(link.target).scheme.lower()
            if scheme in {"http", "https"}:
                if not external:
                    continue
                checked += 1
                url = link.target.split("#", 1)[0]
                if url not in statuses:
                    statuses[url] = status_of(url)
                status = statuses[url]
                if status in UNVERIFIABLE:
                    unverified.append(Problem(link, f"HTTP {status} (refuses automated clients)"))
                elif status == 0:
                    broken.append(Problem(link, "no response"))
                elif status >= 400:
                    broken.append(Problem(link, f"HTTP {status}"))
                continue
            if scheme:  # mailto:, tel:, and the like
                continue
            checked += 1
            reason = check_local(link, root)
            if reason:
                broken.append(Problem(link, reason))
    return broken, unverified, checked


def default_files(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in DEFAULT_GLOBS:
        found.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="Markdown files (default: all docs).")
    parser.add_argument("--external", action="store_true", help="Also request web links.")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    files = [path.resolve() for path in args.files] or default_files(root)
    missing = [path for path in files if not path.is_file()]
    if missing:
        print(f"ERROR: no such file: {missing[0]}", file=sys.stderr)
        return 2
    broken, unverified, checked = check_files(files, root, external=args.external)
    for problem in unverified:
        print(f"UNVERIFIED {problem.render(root)}")
    for problem in broken:
        print(f"BROKEN {problem.render(root)}", file=sys.stderr)
    scope = "relative and web links" if args.external else "relative links"
    if broken:
        print(f"{len(broken)} broken of {checked} {scope} in {len(files)} files.", file=sys.stderr)
        return 1
    note = f", {len(unverified)} unverified" if unverified else ""
    print(f"OK: {checked} {scope} in {len(files)} files{note}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
