# Runbook

How to operate a RepoAtlas deployment: deploy, roll back, pause, resume, back up, restore, and
tear down. Two kinds of deployment:

- **Self-hosted** (Docker Compose, [self-hosting.md](self-hosting.md)). Every procedure in
  this section was run on a local stack on 2026-11-14 (M12 Results).
- **Hosted** (Railway or Fly, and Vercel; [deploy.md](deploy.md)). Not deployed yet: hosting is
  a [live-pass](live-pass.md) item. The hosted procedures say what to do and are marked
  **not yet run**.

In the commands, `$C` stands for:

```bash
C="docker compose -f infra/compose.selfhost.yml --env-file infra/selfhost.env"
```

and the check is:

```bash
python scripts/selfhost_check.py --env-file infra/selfhost.env
```

(add `--only gate` for liveness, the gate, and readiness only; add `--skip-build <snapshot id>`
to ask and analyze an existing snapshot instead of building one).

## Self-hosted

### Deploy or update

Give every release its own image tag, so the previous one stays available for a rollback:

```bash
git pull
export REPOATLAS_VERSION=$(git describe --tags --always)
$C up -d --build --wait
python scripts/selfhost_check.py --env-file infra/selfhost.env --only gate
```

`up` starts the one-shot `migrate` service first (`repoatlas db upgrade`); the API and the
worker start only after it succeeded. If a migration fails, they keep running the old version
on the old schema; read `$C logs migrate`.

Write down the tag you deployed (or put `REPOATLAS_VERSION=…` in `infra/selfhost.env`).

### Roll back

Start the previous release's images. No database downgrade is needed: migrations are backward
compatible with the previous release, the old version's migration step sees a schema it
doesn't know and does nothing, and `/ready` reports `database: ahead`.

```bash
export REPOATLAS_VERSION=<previous tag>
$C up -d --no-build --wait
python scripts/selfhost_check.py --env-file infra/selfhost.env --only gate
```

- If the old images are gone, rebuild them: `git checkout <previous tag>`, then the deploy
  commands.
- Releases before M12's gate (the `m12-done` tag) have a migration step that fails on a newer
  schema. To roll back to one of them, start the services without it:
  `$C up -d --no-build --no-deps --wait api worker web`.
- Rolling back more than one release is not covered by the compatibility rule. Restore the
  backup taken before the upgrade instead.

### Pause and resume

Pausing stops every container and keeps the data. Nothing runs, so nothing is spent.

```bash
$C stop        # pause
$C up -d --no-build --wait   # resume
```

A build that was running goes back to the queue and resumes from its last finished stage. To
pause only answering (and LLM spending), set `REPOATLAS_DAILY_SPEND_CAP_USD=0` in
`infra/selfhost.env` and run `$C up -d`: questions then refuse with the daily-limit message,
and maps, search, and impact analysis keep working.

### Back up

```bash
$C exec -T db pg_dump -U repoatlas -Fc -f /tmp/repoatlas.dump repoatlas
$C cp db:/tmp/repoatlas.dump ./repoatlas-$(date +%Y%m%d).dump
```

The dump is written inside the container and copied out, which is safe in every shell
(PowerShell's `>` corrupts binary output). It holds everything: repositories' text, graphs,
embeddings, answers, and traces. Keep it somewhere private. Back up before every update. The
`repoatlas-data` volume holds only build checkpoints and needs no backup.

### Restore

Into an empty database (a new machine, or after a teardown). Use the release that made the
backup, or a newer one.

```bash
$C up -d --no-build --wait db
$C cp ./repoatlas-YYYYMMDD.dump db:/tmp/restore.dump
$C exec -T db pg_restore -U repoatlas -d repoatlas --no-owner /tmp/restore.dump
$C up -d --no-build --wait
python scripts/selfhost_check.py --env-file infra/selfhost.env --only gate
```

The last `up` runs the migrations from the backup's revision to the current one. To replace a
database that still has data, tear down first (below), then restore.

### Tear down

```bash
$C down              # remove the containers; keep the data
$C down --volumes    # remove the containers and delete all data (cannot be undone)
docker image rm $(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^repoatlas-\(api\|worker\|web\):')
```

Delete `infra/selfhost.env` if the machine is being handed on: it holds the access token and
any API key.

### When something is wrong

| Symptom | Look at | Likely cause and fix |
|---|---|---|
| Every page redirects to `/access` after entering the token | Browser: is the cookie set? | The site is served over HTTPS but the proxy doesn't send `X-Forwarded-Proto`, or the token was changed. Fix the proxy; enter the new token. |
| 401 `unauthorized` from scripts | The token in use | Send `Authorization: Bearer <token>` |
| Answers say the server reached its daily limit | `$C logs api`, event `daily spend cap reached` | The spend cap worked. It resets at midnight UTC; raise `REPOATLAS_DAILY_SPEND_CAP_USD` only on purpose. |
| 429 on builds or questions | The response's `Retry-After` | A per-visitor cap. Daily caps reset at midnight UTC (and when the API restarts). |
| "Answers aren't set up" | `infra/selfhost.env` | No API key and not `offline` |
| `/ready` says `not_migrated` | `$C logs migrate` | A migration failed or never ran: `$C up -d migrate` |
| `/ready` says `ahead` | — | Expected after a rollback |
| A build fails or hangs | `$C logs worker`; the job's error in the app | GitHub rate limit (set `REPOATLAS_GITHUB_TOKEN`), a repository over the size caps, or the worker out of memory (it needs about 2 GB) |
| An answer stream stops halfway | The reverse proxy's settings | Buffering or a short idle timeout; see "Serving other machines" in the guide |

Logs are JSON lines; every API response carries `X-Request-ID`, which is on each log line of
that request. Secrets are never logged.

## Hosted (not yet run)

Hosting is a live-pass item, so these procedures have not been run. They follow the release
workflow and the providers' documented behavior; correct them here when the live pass runs.

### Deploy

Push a version tag (`git tag v0.2.0 && git push origin v0.2.0`). With `DEPLOY_ENABLED` set,
`.github/workflows/release.yml` builds the images, checks them as a stack, publishes them to
GHCR, runs `repoatlas db upgrade`, deploys the backend and the web app, and smoke-checks
`/ready`. Then run the end-to-end check against the public address.

### Roll back

Run the Release workflow by hand (Actions → Release → Run workflow) with `version` set to the
previous tag. It deploys that version's images and web build without rebuilding; the migration
step does nothing on the newer schema. Vercel can also promote the previous deployment from its
dashboard (web app only).

### Pause and resume

- **Railway:** remove the API and worker deployments (or let app sleeping stop them); the
  database keeps running and keeps costing unless it is stopped too. Resume by redeploying.
- **Fly:** `fly scale count 0 --app <app>` for the API and the worker; resume with
  `fly scale count 1`. With `auto_stop_machines`, the API also stops by itself when idle.
- **Vercel:** costs nothing while idle on the Hobby plan. To take the site down, pause the
  project in its settings.
- To stop only LLM spending, set `REPOATLAS_DAILY_SPEND_CAP_USD=0` on the API and redeploy.

### Back up and restore

Open question from the plan, to settle at the live pass: the provider's built-in Postgres
backups, or scheduled dumps. Until then: `pg_dump -Fc "$DATABASE_URL" -f repoatlas.dump` from a
trusted machine before each release, and `pg_restore --no-owner -d "$NEW_DATABASE_URL"
repoatlas.dump` into a new database, followed by a deploy (which migrates it).

### Tear down

Delete the Vercel project, the API and worker services, and the database (after a final
backup); delete the GHCR packages; remove the GitHub secrets and set `DEPLOY_ENABLED` to
`false`; revoke the Anthropic key, the GitHub token, and the provider tokens.
