# Going public: the checklist

> **Status: not done.** RepoAtlas has never been deployed or made public. This is the plan for
> the day the owner decides to. It is live-pass item 26 in
> [`docs/live-pass.md`](../live-pass.md), and nothing here may be reported as done before it
> is.

Going public is one change: unsetting `REPOATLAS_ACCESS_TOKEN` on the API and on the web app.
Everything before that makes sure the deployment is safe to open, and everything after
confirms it stayed that way.

## Before: with the access gate still on

Do these on the hosted deployment, signed in with the token.

1. **The earlier live-pass items are done and written down.** Section A of the
   [live pass order](../live-pass.md#live-pass-order), steps 1 to 7: a live answer, the
   injection gates of every answering mode with zero leaks, hosting with one rollback, the
   hosted end-to-end check, and the gallery.
2. **The limits are the production ones.** The backend has every line of
   [`infra/production.env.example`](../../infra/production.env.example). Read the values back
   from the provider's dashboard; do not trust memory.
3. **The daily spend cap stops answers.** Set `REPOATLAS_DAILY_SPEND_CAP_USD` to `0.01`,
   redeploy, and ask two questions. The second must be refused with the daily-limit message,
   and the API log must show `daily spend cap reached`. Set the cap back to the production
   value and redeploy.
4. **The per-client caps count per visitor.** From one browser, start builds until the daily
   build cap answers 429 with a `Retry-After`. From a second network (a phone on mobile data),
   a build must still be accepted. If the second client is refused too, the API is not seeing
   visitor addresses: check `REPOATLAS_TRUST_PROXY_HEADERS` and that the API is reachable only
   through the web app ([deploy.md](../deploy.md)).
5. **A big repository is refused, not built.** Paste a repository over the size cap; the
   landing page must say it is too large.
6. **The gallery is complete.** Ten cards on the landing page; each opens its map without a
   build. `GET /api/v1/gallery` lists ten entries.
7. **Alerts reach you.** The provider's billing alert and the Anthropic console's spend limit
   are set to amounts you chose, and a test notification arrived.
8. **Rollback is one step.** You know the previous release's version tag and have run the
   rollback once (live-pass item 3).

## The change

9. Remove `REPOATLAS_ACCESS_TOKEN` from the API service and from the web app, and redeploy
   both. Keep the old value somewhere private: putting it back is how the gate returns.

## After

10. **Open the public URL in a private window.** No token page. The landing page loads, a
    gallery card opens, a question gets a cited answer, and "What breaks?" returns a report.
11. **The API is not open wider than the web app.** `GET /api/v1/ready` from outside answers
    through the web app's address only; the backend's own address is not reachable from the
    internet, or answers nothing useful.
12. **Repeat step 10 from a second network.** `scripts/selfhost_check.py` needs an access
    token, so it checks gated deployments only (it ran in step 1); after the gate is gone the
    check is by hand.
13. **Watch the first hour:** the API log for 5xx responses and `daily spend cap reached`,
    the worker log for failed jobs, and the day's spend.
14. Record the date, the release version, and the outcome of steps 3 to 6 and 10 to 12 in the
    Results section of [`docs/live-pass.md`](../live-pass.md) and in M17's Results.

## Putting the gate back

Set `REPOATLAS_ACCESS_TOKEN` again on both services and redeploy. To stop spending without
closing the site, set `REPOATLAS_DAILY_SPEND_CAP_USD=0` on the API instead: maps and impact
analysis keep working, and questions are refused. Both are in the [runbook](../runbook.md).

## What is deliberately not here

- **Publishing the post.** That is the owner's, after the demo is recorded on the public URL
  ([linkedin-post.md](linkedin-post.md), [demo-script.md](demo-script.md)).
- **Switching answering modes off.** There is no such setting yet (it is in the
  [backlog](../backlog.md)), which is why step 1 requires the injection gates of all three
  modes.
