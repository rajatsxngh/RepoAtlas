# MCP server

`repoatlas mcp` serves one snapshot's read-only tools over the
[Model Context Protocol](https://modelcontextprotocol.io), so a coding agent such as Claude Code
can search the code, look up symbols, walk the call graph, read files, see who wrote a file, and
ask what a change would affect. It calls no model and needs no Anthropic API key: every answer
comes from the snapshot's stored graph, chunks, and blobs.

The tools are the ones the built-in agent uses (`repoatlas.agent.tools`), with the same schemas:
`search_code`, `find_definition`, `find_callers`, `find_callees`, `trace_path`, `read_file`,
`get_owners`, `get_impact`.

## What a call returns

- **Text** for the client's model: the result, trimmed to
  `REPOATLAS_AGENT_PER_TOOL_RESULT_TOKENS` (2,000 by default), ending with an evidence list:
  one `<evidence id="src/app.py#L10-L24@abc1234" path="src/app.py" lines="10-24"/>` line per
  piece of code the result is about. The `id` is the compact evidence reference, so it means
  the same thing in every call.
- **Structured content**: the same evidence as data (`id`, `path`, `start_line`, `end_line`,
  `blob_oid`, `symbol_key`, `flags`), and `truncated`.

Everything in a result is text copied from the repository: code, names, paths, author names.
It is untrusted data for the client's model too. Text the injection detector marked is wrapped
in `<flagged reason="…">…</flagged>`, and the server's instructions tell the client so. A call
that can't run (a path outside the snapshot, an unknown symbol, invalid arguments) comes back
as an error result the model can read and correct.

## Local use (stdio)

Find a snapshot ID (`repoatlas build <url>` prints one, and the web app shows it in the address
bar as `/s/<id>`), then register the server with your client. For Claude Code, from the
repository root:

```bash
claude mcp add repoatlas -- uv run --directory backend repoatlas mcp --snapshot <id>
```

On stdio, stdout carries only the protocol; logs and the startup line go to stderr. The server
serves one snapshot until the client disconnects; start another for another snapshot.

## Remote use (Streamable HTTP)

```bash
REPOATLAS_MCP_TOKEN=<a long random value> uv run repoatlas mcp --snapshot <id> --transport http --port 8811
```

The endpoint is `http://<host>:8811/mcp`.

- **A bearer token is required.** The server refuses to start without `REPOATLAS_MCP_TOKEN`,
  and every request must send `Authorization: Bearer <token>`; anything else gets a 401 before
  it reaches a tool. Keep the token in `.env` or the environment, never on a command line that
  ends up in shell history.
- **Calls are rate-limited** per client address: `REPOATLAS_MCP_CALLS_PER_MINUTE` (120 by
  default), with a 429 and `Retry-After` beyond it.
- It listens on `127.0.0.1` unless `--host` says otherwise. On a loopback address the SDK also
  rejects requests whose `Host` or `Origin` isn't local. To serve other machines, bind a
  routable address and put TLS in front (a reverse proxy); the token travels in a header.
- The transport is stateless: each request stands alone, so any number of server processes can
  sit behind a load balancer. The rate limit is per process.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `REPOATLAS_MCP_TOKEN` | unset | The bearer token for the HTTP transport (required for it). |
| `REPOATLAS_MCP_CALLS_PER_MINUTE` | 120 | Requests per client address and minute (HTTP). |
| `REPOATLAS_AGENT_PER_TOOL_RESULT_TOKENS` | 2000 | How much text one result may carry. |

The MCP SDK (`mcp`) is pinned to its 2.x major version in `backend/pyproject.toml`.
