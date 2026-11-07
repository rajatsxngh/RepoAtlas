# Web app

The RepoAtlas web app: Next.js (App Router) with TypeScript, Tailwind CSS, TanStack Query, and Zustand. The map is drawn with React Flow and laid out by elkjs in a web worker.

## Commands

Run these from `frontend/` (Node 22.12 or later).

| Purpose                                                      | Command                              |
| ------------------------------------------------------------ | ------------------------------------ |
| Install                                                      | `npm ci`                             |
| Dev server on http://localhost:3000 (needs the API on :8000) | `npm run dev`                        |
| Dev server with fixtures instead of the API                  | `NEXT_PUBLIC_API_MOCK=1 npm run dev` |
| Lint and formatting check                                    | `npm run lint`                       |
| Format and autofix                                           | `npm run format`                     |
| Type-check                                                   | `npm run typecheck`                  |
| Unit tests (Vitest)                                          | `npm test`                           |
| End-to-end tests (Playwright)                                | `npx playwright test`                |

To run everything at once (Postgres, migrations, the API, a worker, and this app), run `node scripts/dev.mjs` from the repository root. It prints the app's URL (http://localhost:3000, or the next free port) and stops everything on Ctrl+C.

## Settings

| Variable               | Default                 | Meaning                                                    |
| ---------------------- | ----------------------- | ---------------------------------------------------------- |
| `NEXT_PUBLIC_API_URL`  | `http://localhost:8000` | Origin of the API (its routes live under `/api/v1`)        |
| `NEXT_PUBLIC_API_MOCK` | unset                   | `1` serves fixtures from memory instead of calling the API |
| `NEXT_PUBLIC_NODE_CAP` | `300`                   | Most nodes the map shows at once                           |

`NEXT_PUBLIC_*` values are inlined when the app is built, so set them before `npm run build` or `npm run dev`.

## End-to-end tests

`npx playwright test` runs two projects:

- `app` (`e2e/app/`): the web app against the real API and a worker, started by `backend/tests/e2e/stack.py` over local fixture repositories (`repoatlas-e2e/shop`, `repoatlas-e2e/xss`; GitHub is never contacted). It needs Postgres running (`docker compose -f infra/docker-compose.yml up -d`) and creates its own `<database>_e2e` database.
- `mock` (`e2e/mock/`): a mock-mode build, including the stress test on the 10,000-node synthetic graph (every expansion laid out in under 2 seconds, never more than the node cap visible).

`E2E_MOCK_ONLY=1 npx playwright test --project mock` runs the mock project without any services.

## Mock mode

With `NEXT_PUBLIC_API_MOCK=1` the API client answers from memory (`src/lib/api/mock/`), with the API's routes, models, and error model. Builds are simulated: a build request walks the real stage names over a couple of seconds.

| Snapshot | Repository (`https://github.com/repoatlas-fixtures/…`) | What it is                                                   |
| -------- | ------------------------------------------------------ | ------------------------------------------------------------ |
| 1        | `py-small`                                             | The backend's Python golden fixture (also any unknown name)  |
| 2        | `ts-small`                                             | The backend's TypeScript golden fixture                      |
| 3        | `xss`                                                  | HTML and script payloads in names, owners, comments, strings |
| 4        | `synthetic`                                            | 10,000 generated nodes: a deep tree with dense calls         |
| 5        | `broken`                                               | Its build always fails                                       |

The names `private`, `too-large`, `empty`, `missing`, `github-down`, and `rate-limited` answer like the matching API errors. A unit test checks the mock against the backend's own answers for the golden fixtures; after the golden graphs change, refresh both data files:

```bash
npx tsx scripts/import-backend-fixtures.ts
uv run --project ../backend python scripts/export-mock-expectations.py
```

## Safety

Repo content is untrusted. The app renders file names, symbol names, owner names, and code as text, never as HTML: ESLint rejects `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, and `document.write`.

## API types

`src/lib/api/schema.d.ts` holds TypeScript types for the HTTP API, generated from [`backend/openapi.json`](../backend/openapi.json) by `openapi-typescript`. Its version is pinned in `package.json` and `package-lock.json`, so the output only changes when the spec does. Don't edit the file: after changing an API route or model, regenerate both files and commit them.

```bash
scripts/gen_api_types.sh            # Windows: scripts/gen_api_types.ps1
scripts/gen_api_types.sh --check    # changes nothing; fails if either file is stale
```

CI fails when either file is out of date with the code.
