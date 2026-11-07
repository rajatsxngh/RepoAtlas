# Web app

The RepoAtlas web app: Next.js (App Router) with TypeScript, Tailwind CSS, TanStack Query, and Zustand. The map is drawn with React Flow and laid out by elkjs in a web worker.

## Commands

Run these from `frontend/` (Node 22.12 or later).

| Purpose | Command |
|---|---|
| Install | `npm ci` |
| Dev server on http://localhost:3000 (needs the API on :8000) | `npm run dev` |
| Dev server with fixtures instead of the API | `NEXT_PUBLIC_API_MOCK=1 npm run dev` |
| Lint and formatting check | `npm run lint` |
| Format and autofix | `npm run format` |
| Type-check | `npm run typecheck` |
| Unit tests (Vitest) | `npm test` |
| End-to-end tests (Playwright) | `npx playwright test` |

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Origin of the API (its routes live under `/api/v1`) |
| `NEXT_PUBLIC_API_MOCK` | unset | `1` serves fixtures from memory instead of calling the API |
| `NEXT_PUBLIC_NODE_CAP` | `300` | Most nodes the map shows at once |

`NEXT_PUBLIC_*` values are inlined when the app is built, so set them before `npm run build` or `npm run dev`.

## Safety

Repo content is untrusted. The app renders file names, symbol names, owner names, and code as text, never as HTML: ESLint rejects `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, and `document.write`.

## API types

`src/lib/api/schema.d.ts` holds TypeScript types for the HTTP API, generated from [`backend/openapi.json`](../backend/openapi.json) by `openapi-typescript`. Its version is pinned in `package.json` and `package-lock.json`, so the output only changes when the spec does. Don't edit the file: after changing an API route or model, regenerate both files and commit them.

```bash
scripts/gen_api_types.sh            # Windows: scripts/gen_api_types.ps1
scripts/gen_api_types.sh --check    # changes nothing; fails if either file is stale
```

CI fails when either file is out of date with the code.
