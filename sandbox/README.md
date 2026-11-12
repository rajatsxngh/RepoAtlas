# Sandbox

Hardened containers for the five curated benchmark repos. Code from analyzed repos runs here and nowhere else, and only for the recipes in `recipes/` ([decision 0012](../docs/decisions/0012-hardened-sandbox.md)). User-submitted repos are never run.

| Path | What it is |
|---|---|
| `images/python.Dockerfile`, `images/node.Dockerfile` | Base images: upstream images pinned by digest, a non-root user (uid 10001), and pinned SCIP indexers (`scip-python/`, `scip-typescript/` hold their `package-lock.json`). |
| `proxy/` | The allowlist forward proxy (Squid): HTTPS to `pypi.org`, `files.pythonhosted.org`, and `registry.npmjs.org` only; private and host addresses are denied. |
| `recipes/<repo_id>.yaml` | One recipe per benchmark repo: pinned commit, install steps, test command and reporter, SCIP settings, quarantine list. |

## How a repo runs

1. **Build** (`repoatlas sandbox build <recipe>`). The commit's files come from git objects (`fetch_source`) and are streamed to `docker build` as a tar, with no network. A container then runs the install steps on the internal network `repoatlas-sbx-internal`, whose only exit is the proxy. The result is committed as the repo image, and its digest goes to `eval/artifacts/<repo_id>/<commit>/image.json`.
2. **Run** (tests, indexers). Every run uses `hardened_run_args` in `backend/src/repoatlas/sandbox/hardened.py`: no network, all capabilities dropped, `no-new-privileges`, uid 10001, a read-only root with a 512 MB `/tmp`, and limits on pids, memory, CPU and file handles. There are no bind mounts and no Docker socket, and a host-side timer kills the container. Results come back as a tar of `/tmp/out` on stdout.

Docker Desktop on Windows runs every container in one shared WSL2 VM, so these flags are a layer of defense, not a guarantee. That is why only curated repos run here.

## Commands

```bash
cd backend
uv run repoatlas sandbox proxy                          # start the network and proxy (build does this too)
uv run repoatlas sandbox build ../sandbox/recipes/click.yaml
uv run repoatlas sandbox test ../sandbox/recipes/click.yaml    # one full run, summary only
uv run pytest tests/integration/sandbox -q              # needs Docker; skipped in CI unless REPOATLAS_SANDBOX_TESTS=1
```

Base images are about 0.6 GB (Python) and 0.4 GB (Node), and repo images add 0.1–0.6 GB each.
