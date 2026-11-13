# Self-hosting RepoAtlas

Run the whole product on one machine with Docker: Postgres (with pgvector), the API, a worker,
and the web app. Day-to-day operations (update, roll back, pause, back up, restore, tear down)
are in the [runbook](runbook.md). Hosting it on a platform instead is in
[deploy.md](deploy.md).

## What you need

- Docker with Compose v2 (Docker Desktop, or Docker Engine with the compose plugin).
- About 6 GB of free disk for the images, and 4 GB of memory. The backend images carry the
  embedding model (about 0.6 GB); a build embeds on CPU, and the worker needs about 2 GB while
  it does.
- Outbound internet access from the worker, to fetch repositories from GitHub.
- For real answers: an Anthropic API key. Without one the stack still runs with the offline
  stand-in (see [Answers](#answers)).

## Start it

```bash
cp infra/selfhost.env.example infra/selfhost.env
```

Open `infra/selfhost.env` and set the two required values:

- `REPOATLAS_ACCESS_TOKEN`: the token visitors type once. Make it long and random:
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `POSTGRES_PASSWORD`: any random string. It is used only inside the compose network.

`infra/selfhost.env` is ignored by git. Keep it out of backups you share.

```bash
docker compose -f infra/compose.selfhost.yml --env-file infra/selfhost.env up -d --build
```

The first build takes several minutes: it installs the dependencies and downloads the embedding
model into the images. Later starts take seconds.

Open <http://localhost:8080>, enter the access token, and paste a public GitHub URL.

To check the whole path from the command line (the gate, a build, a question, and impact
analysis):

```bash
python scripts/selfhost_check.py --env-file infra/selfhost.env
```

## What runs

| Service | Image | What it does |
|---|---|---|
| `db` | `pgvector/pgvector:pg16` (pinned by digest) | Postgres with the `vector` and `pg_trgm` extensions; data in the `repoatlas-pg` volume |
| `migrate` | `repoatlas-api` | Runs `repoatlas db upgrade` once and exits; the API and the worker start only after it succeeded |
| `api` | `repoatlas-api` | The HTTP API on port 8000, reachable only inside the compose network |
| `worker` | `repoatlas-worker` | Builds snapshots (fetch, parse, graph, embed); checkpoints in the `repoatlas-data` volume |
| `web` | `repoatlas-web` | The web app on port 3000, published as `127.0.0.1:8080`; forwards `/api/v1/*` to the API |

Only the web app is published, and only on this machine. Every process runs as a user without
privileges, with all Linux capabilities dropped.

## Answers

Answering a question calls Claude through the Anthropic API, which costs money per question.

- **With a key:** set `REPOATLAS_ANTHROPIC_API_KEY` in `infra/selfhost.env` and leave
  `REPOATLAS_LLM_TRANSPORT=anthropic`. The [limits](#limits) below cap what a day can cost.
- **Without a key:** set `REPOATLAS_LLM_TRANSPORT=offline`. Every answer is then a labelled
  stand-in that cites the code retrieved for the question and says no model wrote it. It costs
  nothing and shows that the stack works; it does not answer questions. Setting both a key and
  `offline` is refused.
- **With neither:** the map, search, and impact analysis work; asking a question answers
  "Answers aren't set up".

After changing the file, apply it with the same `up -d` command.

## The access gate

With `REPOATLAS_ACCESS_TOKEN` set (the compose file requires it):

- every page redirects to `/access` until the visitor has entered the token once; the browser
  then holds an HTTP-only cookie with a digest of the token, valid for 30 days;
- every API call needs that cookie, or the token itself as `Authorization: Bearer <token>`
  (for scripts);
- `/healthz` (web) and `/api/v1/health` (API, internal) stay open for health checks.

To change the token, edit the file and run `up -d` again; every cookie stops working at once.

## Limits

Defaults, all set in `infra/selfhost.env` (names as in the file):

| Limit | Default | Over it |
|---|---|---|
| `REPOATLAS_DAILY_SPEND_CAP_USD`: all LLM calls per UTC day | $5 | Answers refuse with "daily limit" until midnight UTC |
| `REPOATLAS_ASKS_PER_DAY`: questions per visitor and UTC day | 20 | 429 with `Retry-After` |
| `REPOATLAS_BUILDS_PER_DAY`: build requests per visitor and UTC day | 3 | 429 with `Retry-After` |
| `REPOATLAS_JOB_MAX_RUNNING`: builds running at once | 2 | Further builds wait in the queue |

Hourly limits (20 questions, 5 builds) and repository size caps apply as well;
`infra/production.env.example` lists every limit with tighter values for a deployment other
people can reach. Per-visitor counts are kept in the API's memory, so they start again when the
API restarts; the spend cap is read from the database and survives restarts.

A visitor is told apart by address. The web app reports the address its own reverse proxy
gives it (`X-Real-IP`, or the last entry of `X-Forwarded-For`). **Published directly, without
a reverse proxy, a caller can send those headers and pose as many visitors**, so per-visitor
caps then only slow down honest use. The access token and the daily spend cap do not depend on
addresses and hold either way.

## Serving other machines

The stack listens on `127.0.0.1` only. To let others in, put a reverse proxy with TLS in front
of port 8080 (Caddy, nginx, Traefik, or a tunnel) rather than publishing the port:

- terminate HTTPS at the proxy and forward to `127.0.0.1:8080`;
- have it set `X-Forwarded-Proto` (so the access cookie is marked `Secure`) and overwrite
  `X-Forwarded-For` or `X-Real-IP` with the caller's address;
- don't buffer responses for `/api/v1/` (answers stream as server-sent events), and allow idle
  connections of at least 60 seconds; the API sends a keep-alive comment every 15 seconds;
- set `REPOATLAS_PUBLIC_ORIGIN` in `infra/selfhost.env` to the public address.

A Caddy example:

```
atlas.example.org {
    reverse_proxy 127.0.0.1:8080 {
        header_up X-Real-IP {remote_host}
        flush_interval -1
    }
}
```

Keep the URL unlisted and share the token only with people who should have access.

## Settings

Everything the stack reads is in `infra/selfhost.env.example`, with comments.
[deploy.md](deploy.md) lists every environment variable of each service.

## Troubleshooting

- **`set REPOATLAS_ACCESS_TOKEN in infra/selfhost.env`**: the file is missing a required
  value, or `--env-file` was left out of the command.
- **The web app says it can't reach RepoAtlas:** `docker compose … ps` shows whether `api` is
  healthy; `docker compose … logs api` shows why not.
- **A build stays queued:** look at `docker compose … logs worker`. Two builds run at once by
  default; others wait.
- **GitHub rate limits:** repository checks use GitHub's API, 60 requests an hour without a
  token. Set `REPOATLAS_GITHUB_TOKEN` (a token with no scopes is enough).
- **Port 8080 is taken:** set `REPOATLAS_WEB_PORT` in the file.
