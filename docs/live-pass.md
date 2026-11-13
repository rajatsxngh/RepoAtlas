# Live pass

Checks that need spending (Anthropic API calls or hosting) and are deferred until the build is
finished and the owner approves it (decision
[0020](decisions/0020-build-without-spend-live-pass.md)). Each item is **pending** until it is
run and its result is written here and in its milestone's Results. Nothing on this list may be
reported as passed before then.

| # | From | Check | Needs | Status |
|---|---|---|---|---|
| 1 | M12 | Backend on Railway or Fly with managed Postgres; `vector` and `pg_trgm` confirmed | Hosting account and bill | Pending |
| 2 | M12 | Frontend on Vercel behind the access gate | Vercel project | Pending |
| 3 | M12 | A release through `release.yml` runs migrations; a rollback to the previous release works | Items 1 and 2 | Pending |
| 4 | M12 | On the hosted deployment: a repo builds, a question gets a cited answer from the live model, impact analysis returns a report | Items 1–3, API key, a few cents of calls | Pending |
