"""Generated change sets: random edits to the fixture repositories, linked incrementally in
chains, must always equal a full link (the safety net for the invalidation rules).

The edits are crude on purpose (deleted lines, swapped files, appended definitions that reuse
names already called somewhere), so they produce syntax errors, dangling imports, shadowed
modules, and name clashes that nobody would script by hand.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from tests.unit.graph.helpers import parse_files
from tests.unit.pipeline.incremental_helpers import Linked, assert_same, link, relink
from tests.unit.pipeline.test_incremental import SHOP, WEB

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "repos"
SEEDS = range(40)
STEPS = 4


def read_repo(name: str) -> dict[str, bytes]:
    root = FIXTURES / name
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


REPOS: dict[str, dict[str, bytes]] = {
    "py_small": read_repo("py_small"),
    "ts_small": read_repo("ts_small"),
    "shop": {path: text.encode() for path, text in SHOP.items()},
    "web": {path: text.encode() for path, text in WEB.items()},
}

_SOURCE = (".py", ".ts", ".tsx", ".js")


def _sources(files: dict[str, bytes]) -> list[str]:
    return sorted(path for path in files if path.endswith(_SOURCE))


def _called_names(files: dict[str, bytes]) -> list[str]:
    names = {call.name for parsed in parse_files(files).files for call in parsed.calls if call.name}
    return sorted(name for name in names if name.isidentifier()) or ["run"]


def _definition(path: str, name: str, number: int) -> bytes:
    """A class with a method called ``name`` and a function that calls it."""
    if path.endswith(".py"):
        text = (
            f"\n\nclass Gen{number}:\n    def {name}(self, *args):\n        return args\n\n\n"
            f"def gen_{number}(thing):\n    return thing.{name}()\n"
        )
    else:
        text = (
            f"\n\nexport class Gen{number} {{\n  {name}(...args) {{\n    return args;\n  }}\n}}\n\n"
            f"export function gen{number}(thing) {{\n  return thing.{name}();\n}}\n"
        )
    return text.encode()


def mutate(files: dict[str, bytes], rng: random.Random, number: int) -> dict[str, bytes]:
    """One random edit; the result always differs from ``files`` or the edit is retried."""
    for _ in range(20):
        out = dict(files)
        sources = _sources(out)
        if not sources:
            return out
        path = rng.choice(sources)
        action = rng.choice(
            [
                "delete",
                "rename",
                "copy",
                "append",
                "cut_lines",
                "swap",
                "import",
                "truncate",
                "new",
                "alias",
                "alias",
                "constructor",
                "constructor",
            ]
        )
        extension = "." + path.rsplit(".", 1)[-1]
        folder = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
        if action == "delete" and len(sources) > 2:
            del out[path]
        elif action == "rename":
            out[f"{folder}moved_{number}{extension}"] = out.pop(path)
        elif action == "copy":
            out[f"{folder}copy_{number}{extension}"] = out[path]
        elif action == "append":
            out[path] += _definition(path, rng.choice(_called_names(out)), number)
        elif action == "cut_lines":
            lines = out[path].splitlines(keepends=True)
            if len(lines) > 2:
                start = rng.randrange(len(lines))
                del lines[start : start + rng.randint(1, 4)]
                out[path] = b"".join(lines)
        elif action == "swap":
            other = rng.choice(sources)
            out[path], out[other] = out[other], out[path]
        elif action == "import":
            other = rng.choice(sources)
            stem = other.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            name = rng.choice(_called_names(out))
            if path.endswith(".py"):
                line = f"from {stem} import {name}\nfrom .{stem} import {name} as local_{number}\n"
            else:
                line = f"import {{ {name} }} from './{stem}';\n"
            out[path] = line.encode() + out[path]
        elif action == "truncate":
            out[path] = out[path][: len(out[path]) // 2]
        elif action == "new":
            stem = rng.choice(["util", "index", "__init__", f"mod_{number}"])
            name = rng.choice(_called_names(out))
            out[f"{folder}{stem}{extension}"] = _definition(path, name, number).lstrip()
        elif action == "alias":
            out[path] = _alias(out, path, rng, number) + out[path]
        elif action == "constructor":
            out[path] = _with_constructor(out[path], path, rng)
        if out != files:
            return out
    return files


def _alias(files: dict[str, bytes], path: str, rng: random.Random, number: int) -> bytes:
    """An import of some class or function under another name, and a function that calls it:
    a call whose name is nobody's symbol name."""
    python = path.endswith(".py")
    symbols = [
        (parsed.path, symbol.name)
        for parsed in parse_files(files).files
        if parsed.path != path and parsed.path.endswith(".py") == python
        for symbol in parsed.symbols
        if symbol.parent is None and symbol.kind in ("class", "function")
        if symbol.name.isidentifier()
    ]
    if not symbols:
        return b""
    target, name = rng.choice(sorted(symbols))
    if python:
        dotted = target.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
        dotted = dotted.removeprefix("src.")
        text = (
            f"from {dotted} import {name} as alias_{number}\n\n\n"
            f"def use_alias_{number}():\n    return alias_{number}()\n\n\n"
        )
    else:
        here = path.split("/")[:-1]
        there = target.rsplit(".", 1)[0].split("/")
        while here and there and here[0] == there[0]:
            here, there = here[1:], there[1:]
        specifier = "/".join([".." for _ in here] or ["."]) + "/" + "/".join(there)
        text = (
            f"import {{ {name} as alias{number} }} from '{specifier}';\n\n"
            f"export function useAlias{number}() {{\n  return new alias{number}();\n}}\n\n"
        )
    return text.encode()


def _with_constructor(data: bytes, path: str, rng: random.Random) -> bytes:
    """Give one class of the file a constructor (inserted right after its first line)."""
    lines = data.splitlines(keepends=True)
    python = path.endswith(".py")
    heads = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith((b"class ", b"export class "))
        and line.rstrip().endswith(b":" if python else b"{")
    ]
    if not heads:
        return data
    index = rng.choice(heads)
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    body = (
        indent + b"    def __init__(self):\n" + indent + b"        self.made = True\n\n"
        if python
        else indent
        + b"  constructor() {\n"
        + indent
        + b"    this.made = true;\n"
        + indent
        + b"  }\n"
    )
    return b"".join([*lines[: index + 1], body, *lines[index + 1 :]])


@pytest.mark.parametrize("repo", sorted(REPOS))
@pytest.mark.parametrize("seed", SEEDS)
def test_generated_edits_equal_a_full_link(repo: str, seed: int) -> None:
    rng = random.Random(f"{repo}:{seed}")
    files = REPOS[repo]
    previous = link(files)
    for step in range(STEPS):
        files = mutate(files, rng, seed * 10 + step)
        after = relink(previous, files)
        assert_same(after)
        # The next step builds on the incremental result, as real builds do.
        previous = Linked(files, parse_files(files), after.incremental)


def test_the_generated_edits_exercise_reuse_and_every_rule() -> None:
    """The generator is only a safety net if it actually reuses files and trips each rule."""
    reused = 0
    seen: set[str] = set()
    for repo, base in REPOS.items():
        for seed in SEEDS:
            rng = random.Random(f"{repo}:{seed}")
            files = base
            previous = link(files)
            for step in range(STEPS):
                files = mutate(files, rng, seed * 10 + step)
                after = relink(previous, files)
                reused += len(after.reused)
                seen.update(after.reasons.values())
                previous = Linked(files, parse_files(files), after.incremental)
    assert reused > 500
    assert {"changed", "imports", "dependency", "name_match"} <= seen
