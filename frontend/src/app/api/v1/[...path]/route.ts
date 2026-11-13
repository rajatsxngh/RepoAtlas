/** Forwards `/api/v1/*` to the RepoAtlas API (see `src/lib/apiProxy.ts`). */

import { forward } from "@/lib/apiProxy";

export const dynamic = "force-dynamic";

export function GET(request: Request): Promise<Response> {
  return forward(request);
}

export function POST(request: Request): Promise<Response> {
  return forward(request);
}
