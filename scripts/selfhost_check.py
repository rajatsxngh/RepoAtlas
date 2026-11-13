#!/usr/bin/env python3
"""End-to-end check of a running RepoAtlas deployment (standard library only).

Against the web app's address it checks, in order:

  1. liveness, and that the access gate rejects a missing or wrong token;
  2. the token page: entering the token sets the access cookie, and the cookie opens the app;
  3. readiness (the database is migrated);
  4. a build: the repository is fetched, parsed, and stored until its snapshot is ready;
  5. a question: the answer arrives over server-sent events with citations;
  6. impact analysis on a symbol of the snapshot;
  7. the stored trace of the answer.

Usage:
  python scripts/selfhost_check.py --env-file infra/selfhost.env
  python scripts/selfhost_check.py --base-url https://atlas.example.org --token "$TOKEN"

The token is read from --token, the REPOATLAS_ACCESS_TOKEN variable, or --env-file, and is
never printed. With --expect-offline the answer must be the offline stand-in's (no model);
with --expect-live it must not be. Exit code 0 when every step passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ACCESS_COOKIE = "repoatlas_access"
OFFLINE_MARK = "Offline stand-in"
DEFAULT_REPO = "https://github.com/pallets/itsdangerous"
DEFAULT_QUESTION = "How is a signature created and checked?"
SYMBOL_KINDS = {"function", "method", "class"}


class CheckFailed(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Reply:
    def __init__(self, status: int, headers: Any, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def call(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 60.0,
) -> Reply:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return Reply(response.status, response.headers, response.read())
    except urllib.error.HTTPError as error:
        return Reply(error.code, error.headers, error.read())
    except urllib.error.URLError as error:
        raise CheckFailed(f"{method} {url}: {error.reason}") from None


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def events(body: str) -> list[tuple[str, Any]]:
    """The server-sent events of a response body; comment lines (keep-alives) are skipped."""
    found = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            found.append((name, json.loads("\n".join(data))))
    return found


class Check:
    def __init__(self, base_url: str, token: str, *, verbose: bool = True) -> None:
        self.base = base_url.rstrip("/")
        self.api = f"{self.base}/api/v1"
        self.token = token
        self.bearer = {"Authorization": f"Bearer {token}"}
        self.verbose = verbose
        self.summary: dict[str, Any] = {"base_url": self.base}

    def say(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr, flush=True)

    def post(self, path: str, payload: Any, *, timeout: float = 60.0) -> Reply:
        headers = {**self.bearer, "Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8")
        return call("POST", f"{self.api}{path}", headers=headers, body=body, timeout=timeout)

    def get(self, path: str) -> Reply:
        return call("GET", f"{self.api}{path}", headers=self.bearer)

    # --- steps ------------------------------------------------------------------------------

    def gate(self) -> None:
        live = call("GET", f"{self.base}/healthz")
        expect(live.status == 200, f"/healthz answered {live.status}")
        missing = call("GET", f"{self.api}/ready")
        expect(missing.status == 401, f"no token: /ready answered {missing.status}, not 401")
        expect(missing.json()["error"]["code"] == "unauthorized", "no token: wrong error code")
        wrong = call("GET", f"{self.api}/ready", headers={"Authorization": "Bearer wrong-token"})
        expect(wrong.status == 401, f"wrong token: /ready answered {wrong.status}, not 401")
        page = call("GET", f"{self.base}/")
        location = urllib.parse.urlsplit(page.headers.get("Location") or "")
        expect(
            page.status == 307 and location.path == "/access",
            f"no cookie: / answered {page.status}, not a redirect to /access",
        )
        self.say("ok  gate: a missing or wrong token is rejected (401, redirect to /access)")

    def token_page(self) -> None:
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        refused = call(
            "POST",
            f"{self.base}/access/enter",
            headers=form,
            body=urllib.parse.urlencode({"token": "wrong-token"}).encode(),
        )
        expect(
            refused.status == 303 and refused.headers.get("Set-Cookie") is None,
            "a wrong token on the token page was not refused",
        )
        entered = call(
            "POST",
            f"{self.base}/access/enter",
            headers=form,
            body=urllib.parse.urlencode({"token": self.token, "next": "/"}).encode(),
        )
        cookie = entered.headers.get("Set-Cookie") or ""
        expect(entered.status == 303, f"the token page answered {entered.status}, not 303")
        expect(cookie.startswith(f"{ACCESS_COOKIE}="), "no access cookie was set")
        expect("HttpOnly" in cookie, "the access cookie is not HTTP-only")
        expect(self.token not in cookie, "the access cookie holds the token itself")
        pair = cookie.split(";", 1)[0]
        home = call("GET", f"{self.base}/", headers={"Cookie": pair})
        expect(home.status == 200, f"with the cookie, / answered {home.status}")
        via_cookie = call("GET", f"{self.api}/ready", headers={"Cookie": pair})
        expect(via_cookie.status == 200, f"with the cookie, /ready answered {via_cookie.status}")
        self.say("ok  token page: the token sets an HTTP-only cookie that opens the app")

    def ready(self) -> None:
        reply = self.get("/ready")
        expect(reply.status == 200, f"/ready answered {reply.status}: {reply.text[:200]}")
        status = reply.json()
        self.summary["ready"] = status
        self.say(f"ok  ready: database {status['database']} at revision {status['revision']}")

    def build(self, repo: str, timeout_s: float) -> int:
        accepted = self.post("/repos", {"url": repo})
        expect(accepted.status == 202, f"build request: {accepted.status} {accepted.text[:300]}")
        snapshot_id = int(accepted.json()["snapshot_id"])
        self.say(f"..  build: snapshot {snapshot_id} for {repo}")
        deadline = time.monotonic() + timeout_s
        last = ""
        while True:
            snapshot = self.get(f"/snapshots/{snapshot_id}").json()
            status = snapshot["status"]
            job = snapshot.get("job") or {}
            stage = f"{status} {job.get('stage') or ''} {float(job.get('progress') or 0):.0%}"
            if stage != last:
                self.say(f"..  build: {stage}")
                last = stage
            if status == "ready":
                break
            expect(status != "failed", f"the build failed: {snapshot.get('error')}")
            expect(time.monotonic() < deadline, f"the build was not ready in {timeout_s:.0f} s")
            time.sleep(3)
        stats = snapshot.get("stats") or {}
        self.summary["build"] = {
            "snapshot_id": snapshot_id,
            "repo": repo,
            "commit": snapshot["commit_sha"],
            "embedding_model": snapshot.get("embedding_model"),
            "stats": {key: stats[key] for key in sorted(stats) if isinstance(stats[key], int)},
        }
        self.say(f"ok  build: snapshot {snapshot_id} is ready at {snapshot['commit_sha'][:12]}")
        return snapshot_id

    def ask(self, snapshot_id: int, question: str, offline: bool | None) -> None:
        reply = self.post(f"/snapshots/{snapshot_id}/ask", {"question": question}, timeout=300.0)
        expect(reply.status == 200, f"ask: {reply.status} {reply.text[:300]}")
        content_type = reply.headers.get("Content-Type") or ""
        expect(content_type.startswith("text/event-stream"), f"ask answered {content_type}")
        stream = events(reply.text)
        names = [name for name, _ in stream]
        expect("error" not in names, f"ask failed: {dict(stream).get('error')}")
        expect(names[-2:] == ["answer", "done"], f"unexpected events: {names}")
        answer = dict(stream)["answer"]
        done = dict(stream)["done"]
        expect(not answer["refused"], f"the answer was refused: {answer.get('refusal_reason')}")
        expect(len(answer["citations"]) >= 1, "the answer has no citations")
        is_offline = OFFLINE_MARK in answer["answer_markdown"]
        if offline is True:
            expect(is_offline, "expected the offline stand-in, but a model answered")
            expect(answer["cost_usd"] == 0, "the offline stand-in reported a cost")
        if offline is False:
            expect(not is_offline, "expected a model's answer, but got the offline stand-in")
        cited = sorted(
            {
                f"{c['ref']['path']}:{c['ref']['start_line']}-{c['ref']['end_line']}"
                for c in answer["citations"]
            }
        )
        self.summary["ask"] = {
            "question": question,
            "answered_by": "offline stand-in (no model)" if is_offline else "model",
            "claims": len(answer["claims"]),
            "citations": cited,
            "cost_usd": answer["cost_usd"],
            "run_id": done["run_id"],
            "trace_id": done["trace_id"],
            "steps": [data["step"] for name, data in stream if name == "status"],
        }
        who = "offline stand-in" if is_offline else "model"
        self.say(f"ok  ask: a cited answer ({who}), {len(cited)} citations, ${answer['cost_usd']}")
        trace = self.get(f"/traces/{done['trace_id']}")
        expect(trace.status == 200, f"the answer's trace answered {trace.status}")
        kinds = [span["kind"] for span in trace.json()["spans"]]
        expect("retrieval" in kinds and "llm" in kinds, f"the trace has spans {kinds}")
        self.summary["ask"]["trace_spans"] = kinds
        stored = self.get(f"/qa/{done['run_id']}")
        expect(stored.status == 200, f"the stored run answered {stored.status}")
        self.say(f"ok  trace: {len(kinds)} spans stored for the answer")

    def impact(self, snapshot_id: int) -> None:
        overview = self.get(f"/snapshots/{snapshot_id}/overview")
        expect(overview.status == 200, f"overview: {overview.status}")
        data = overview.json()
        ranked = [entry["node"] for entry in data.get("core_symbols", [])]
        candidates = [
            n for n in [*ranked, *data.get("public_symbols", [])] if n.get("kind") in SYMBOL_KINDS
        ]
        expect(bool(candidates), "the snapshot has no function, method, or class to analyze")
        target = candidates[0]
        reply = self.post(f"/snapshots/{snapshot_id}/impact", {"symbol_key": target["key"]})
        expect(reply.status == 200, f"impact: {reply.status} {reply.text[:300]}")
        report = reply.json()
        groups: dict[str, int] = {}
        for item in report["items"]:
            groups[item["group"]] = groups.get(item["group"], 0) + 1
        expect(isinstance(report["run_id"], int), "the impact run was not stored")
        self.summary["impact"] = {
            "target": target["key"],
            "items": len(report["items"]),
            "groups": groups,
            "unknown_sites": len(report["unknown"]),
            "run_id": report["run_id"],
        }
        self.say(
            f"ok  impact: {target['name']} affects {len(report['items'])} symbols {groups}, "
            f"{len(report['unknown'])} unknown sites"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base-url", default=None, help="The web app's address.")
    parser.add_argument("--token", default=None, help="The access token (or use --env-file).")
    parser.add_argument(
        "--env-file", type=Path, default=None, help="For example infra/selfhost.env."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="A small public GitHub repository.")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--build-timeout", type=float, default=1800.0, help="Seconds.")
    parser.add_argument(
        "--skip-build",
        type=int,
        default=None,
        metavar="SNAPSHOT_ID",
        help="Use this ready snapshot instead of building.",
    )
    parser.add_argument(
        "--only",
        choices=["gate"],
        default=None,
        help="gate: stop after the gate, the token page, and readiness.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--expect-offline", action="store_true")
    mode.add_argument("--expect-live", action="store_true")
    parser.add_argument("--json", type=Path, default=None, help="Write the summary here.")
    args = parser.parse_args()

    env = read_env_file(args.env_file) if args.env_file else {}
    token = (
        args.token or os.environ.get("REPOATLAS_ACCESS_TOKEN") or env.get("REPOATLAS_ACCESS_TOKEN")
    )
    if not token:
        print(
            "No access token: pass --token, --env-file, or REPOATLAS_ACCESS_TOKEN.", file=sys.stderr
        )
        return 1
    base = args.base_url or f"http://localhost:{env.get('REPOATLAS_WEB_PORT') or 8080}"
    offline = True if args.expect_offline else False if args.expect_live else None

    check = Check(base, token)
    try:
        check.gate()
        check.token_page()
        check.ready()
        if args.only != "gate":
            snapshot_id = args.skip_build or check.build(args.repo, args.build_timeout)
            check.ask(snapshot_id, args.question, offline)
            check.impact(snapshot_id)
    except CheckFailed as failure:
        print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    output = json.dumps(check.summary, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
