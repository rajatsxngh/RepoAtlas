#!/usr/bin/env python3
"""A manual check of the MCP server (AGT-7): start `repoatlas mcp --snapshot <id>` as a
subprocess, connect to it over stdio with the MCP SDK's client, and print what its tools return.

It calls no model and costs nothing. Run it from `backend/` so the SDK is importable:

    cd backend
    uv run python ../scripts/mcp_check.py --snapshot 20 --symbol Session.send
    uv run python ../scripts/mcp_check.py --snapshot 20 --call get_owners '{"path": "setup.py"}'

With `--symbol`, it asks the three questions a coding agent starts with: where is it defined,
who calls it, and what could break if it changes. `--call` (repeatable) runs any tool with JSON
arguments instead. Exit code 0 when every call returned a result, 1 when any was an error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

BACKEND = Path(__file__).resolve().parent.parent / "backend"
CUT = "\n[...]"  # ASCII only around a result: a Windows console may not print anything else


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--snapshot", type=int, required=True, help="A ready snapshot's ID.")
    parser.add_argument("--symbol", help="A symbol name to look up, then follow.")
    parser.add_argument(
        "--call",
        nargs=2,
        action="append",
        metavar=("TOOL", "JSON"),
        default=[],
        help="A tool and its arguments as JSON (repeatable).",
    )
    parser.add_argument("--chars", type=int, default=1500, help="Characters of each result shown.")
    return parser.parse_args()


def show(name: str, args: dict[str, Any], result: Any, seconds: float, chars: int) -> None:
    text = result.content[0].text if result.content else ""
    structured = result.structured_content or {}
    evidence = structured.get("evidence", [])
    print(
        f"\n=== {name} {json.dumps(args)}\n"
        f"    {seconds:.2f} s | error={result.is_error} | {len(text)} characters | "
        f"{len(evidence)} evidence | truncated={structured.get('truncated')} | "
        f"flags={structured.get('flags')}"
    )
    print(text[:chars] + (CUT if len(text) > chars else ""))


async def run(options: argparse.Namespace) -> int:
    server = StdioServerParameters(
        command="uv",
        args=[
            "run",
            "--directory",
            str(BACKEND),
            "repoatlas",
            "mcp",
            "--snapshot",
            str(options.snapshot),
        ],
    )
    failed = 0
    async with Client(server) as client:
        tools = [tool.name for tool in (await client.list_tools()).tools]
        print(f"Connected over stdio. {len(tools)} tools: {', '.join(tools)}")

        async def call(name: str, args: dict[str, Any]) -> Any:
            nonlocal failed
            started = time.perf_counter()
            result = await client.call_tool(name, args)
            show(name, args, result, time.perf_counter() - started, options.chars)
            failed += bool(result.is_error)
            return result

        for name, raw in options.call:
            await call(name, json.loads(raw))
        if options.symbol:
            found = await call("find_definition", {"name": options.symbol})
            keys = [
                item["symbol_key"]
                for item in (found.structured_content or {}).get("evidence", [])
                if item.get("symbol_key")
            ]
            if keys:
                await call("find_callers", {"symbol_key": keys[0]})
                await call("get_impact", {"symbol_key": keys[0], "limit": 8})
            else:
                print(f"\nNo symbol named {options.symbol!r}; nothing to follow.")
                failed += 1
    return 1 if failed else 0


def main() -> None:
    options = arguments()
    if not options.symbol and not options.call:
        sys.exit("Give --symbol NAME, or --call TOOL JSON (see --help).")
    sys.exit(asyncio.run(run(options)))


if __name__ == "__main__":
    main()
