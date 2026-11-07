# Web app

The Next.js web app (map, code viewer, ask and impact panels) will live here. See [`docs/milestones/`](../docs/milestones/README.md) for when and how it is built.

## API types

`src/lib/api/schema.d.ts` holds TypeScript types for the HTTP API, generated from [`backend/openapi.json`](../backend/openapi.json) by `openapi-typescript`. Its version is pinned in `package.json` and `package-lock.json`, so the output only changes when the spec does. Don't edit the file: after changing an API route or model, regenerate both files and commit them.

```bash
scripts/gen_api_types.sh            # Windows: scripts/gen_api_types.ps1
scripts/gen_api_types.sh --check    # changes nothing; fails if either file is stale
```

CI fails when either file is out of date with the code.
