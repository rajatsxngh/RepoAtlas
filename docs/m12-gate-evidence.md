# M12 gate evidence (2026-11-14)

What was run on the local self-host stack for the M12 gate, with the output. The stack is
`infra/compose.selfhost.yml` on Docker 29.9.2 (Compose v5.6.1), Windows 11, with the production
images built from the branch, the real embedding model (`jinaai/jina-embeddings-v2-base-code`,
inside the images), and `REPOATLAS_LLM_TRANSPORT=offline` (no model, no API key, $0).

Nothing here is a hosted deployment or a live model answer. Those are pending in
[`docs/live-pass.md`](live-pass.md).

## Images

| Image | Runs as | Size | Notes |
|---|---|---|---|
| `repoatlas-api` | `10001:10001` | 1.96 GB | The embedding model is 615 MB of it (`/opt/models`) |
| `repoatlas-worker` | `10001:10001` | 2.08 GB | The same, plus git |
| `repoatlas-web` | `node` | 401 MB | Next.js standalone server |

Base images are pinned by digest (`python:3.12-slim-bookworm`, `ghcr.io/astral-sh/uv:0.12.3`,
`node:22-bookworm-slim`, `pgvector/pgvector:pg16`).

## End-to-end check on a fresh stack

`python scripts/selfhost_check.py --env-file infra/selfhost.env --expect-offline`, after
`up -d` on empty volumes:

```
migrate-1  | INFO alembic.runtime.migration: Running upgrade 0007_impact_runs -> 0008_spans_llm_started_at, An index for the daily spend total.
migrate-1  | Database is at revision 0008_spans_llm_started_at.
ok  gate: a missing or wrong token is rejected (401, redirect to /access)
ok  token page: the token sets an HTTP-only cookie that opens the app
ok  ready: database ok at revision 0008_spans_llm_started_at
..  build: snapshot 1 for https://github.com/pallets/itsdangerous
..  build: building fetch 0%
..  build: building embed 75%
..  build: building embed 85%
..  build: building embed 95%
..  build: ready publish 100%
ok  build: snapshot 1 is ready at 672971d66a2e
ok  ask: a cited answer (offline stand-in), 3 citations, $0.0
ok  trace: 4 spans stored for the answer
ok  impact: want_bytes affects 56 symbols {'caller': 30, 'test': 24, 'public_api': 2}, 0 unknown sites
```

- **Build:** `pallets/itsdangerous` at `672971d`, fetched from GitHub by the worker container,
  embedded with the model inside the image (`HF_HUB_OFFLINE=1`: nothing was downloaded at run
  time).
- **Question:** "How is a signature created and checked?" The stream gave the steps
  `retrieving`, `answering`, `validating`, then an answer with 3 claims citing
  `src/itsdangerous/signer.py:20-22`, `:24-28`, and `:36-37`, all valid. The answer text starts
  with the offline notice: no model wrote it. The stored trace has `answer`, `retrieval`,
  `llm`, and `validation` spans; cost $0.
- **Impact:** `py:src/itsdangerous/encoding.py:want_bytes:function` reaches 56 symbols (30
  callers, 24 tests, 2 public APIs), stored as impact run 1.

## Rollback

Three releases on one database: `prev` (commit `d631d47`, newest migration 0007), `head` (the
gate's code, 0008), and `next` (`head` plus a stand-in migration 0009, built from a scratch
copy and never committed).

```
== 1. release 'next' (adds migration 0009): up -d
migrate-1  | Database is at revision 0009_rollback_probe.
ok  ready: database ok at revision 0009_rollback_probe
== 2. roll back to 'head' (knows 0008): up -d
migrate-1  | Database is at revision 0009_rollback_probe, which this version doesn't know: a newer release migrated it. Nothing to do.
ok  gate: a missing or wrong token is rejected (401, redirect to /access)
ok  token page: the token sets an HTTP-only cookie that opens the app
ok  ready: database ahead at revision 0009_rollback_probe
ok  ask: a cited answer (offline stand-in), 3 citations, $0.0
ok  trace: 4 spans stored for the answer
ok  impact: want_bytes affects 56 symbols {'caller': 30, 'test': 24, 'public_api': 2}, 0 unknown sites
== 3. roll back further to 'prev' (commit d631d47, knows 0007; services only)
ok  gate: a missing or wrong token is rejected (401, redirect to /access)
ok  token page: the token sets an HTTP-only cookie that opens the app
ok  ready: database ahead at revision 0009_rollback_probe
ok  ask: a cited answer (offline stand-in), 3 citations, $0.0
ok  trace: 4 spans stored for the answer
ok  impact: want_bytes affects 56 symbols {'caller': 30, 'test': 24, 'public_api': 2}, 0 unknown sites
```

Step 2 is the runbook's rollback (`REPOATLAS_VERSION=<previous> … up -d --no-build`). Step 3
runs code from before migration 0008 on the newer schema; its migration step predates the fix
that tolerates a newer schema, so the services were started without it (`--no-deps`), as the
runbook says for releases before this gate.

## Runbook procedures

Run on the same stack, in this order:

```
== backup
740177 bytes
== pause (stop everything, keep data)
web answers: nothing (paused)
== resume
ok  ready: database ahead at revision 0009_rollback_probe
== teardown (delete containers and data)
volumes left: 0
== restore into a new database
migrate-1  | Database is at revision 0009_rollback_probe, which this version doesn't know: a newer release migrated it. Nothing to do.
ok  gate: a missing or wrong token is rejected (401, redirect to /access)
ok  token page: the token sets an HTTP-only cookie that opens the app
ok  ready: database ahead at revision 0009_rollback_probe
ok  ask: a cited answer (offline stand-in), 3 citations, $0.0
ok  trace: 4 spans stored for the answer
ok  impact: want_bytes affects 56 symbols {'caller': 30, 'test': 24, 'public_api': 2}, 0 unknown sites
```

After the restore the snapshot built before the teardown answered a question and an impact
request again, with no rebuild.

## Found by running it

- **The token page redirect returned 500** in the built web image: Next.js rejects a relative
  `Location` from `proxy.ts`. Unit tests passed; the stack check caught it (`c59a5ab`).
- **A rolled-back release could not start:** its `repoatlas db upgrade` failed on a schema it
  didn't know, and `/ready` reported `not_migrated`. Both now treat a newer schema as fine
  (`4bdbaf0`, `ebdd171`).
