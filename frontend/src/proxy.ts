/**
 * The private access gate (Next.js 16 calls this file "proxy"; it was "middleware" before).
 *
 * With `REPOATLAS_ACCESS_TOKEN` set, every request needs the access cookie: pages redirect to
 * `/access`, API calls get a 401 in the API's error model (API calls may also carry the token as a
 * Bearer header, for scripts). Without it, nothing is gated.
 * See `src/lib/access.ts`.
 */

import { NextResponse, type NextRequest } from "next/server";
import {
  ACCESS_COOKIE,
  ACCESS_PATH,
  accessToken,
  cookieOpens,
  isOpenPath,
  safeEqual,
  safeNext,
} from "@/lib/access";
import { errorResponse } from "@/lib/apiProxy";

export async function proxy(request: NextRequest): Promise<Response> {
  const token = accessToken();
  const { pathname, search } = request.nextUrl;
  if (token === null || isOpenPath(pathname)) return NextResponse.next();
  if (await cookieOpens(request.cookies.get(ACCESS_COOKIE)?.value, token)) {
    return NextResponse.next();
  }
  if (pathname.startsWith("/api/")) {
    // Scripts and probes may send the token itself, as they would to the API directly.
    const bearer = /^Bearer\s+(.+)$/i.exec(request.headers.get("authorization") ?? "")?.[1];
    if (bearer !== undefined && safeEqual(bearer.trim(), token)) return NextResponse.next();
    return errorResponse(
      401,
      "unauthorized",
      "This deployment is private: an access token is required.",
    );
  }
  // A relative Location: behind a reverse proxy the server doesn't know its public origin.
  const next = safeNext(`${pathname}${search}`);
  const location = next === "/" ? ACCESS_PATH : `${ACCESS_PATH}?next=${encodeURIComponent(next)}`;
  return new Response(null, { status: 307, headers: { Location: location } });
}

export const config = {
  // Static assets carry nothing private and must load on the token page too.
  matcher: ["/((?!_next/static|_next/image|icon.svg|favicon.ico).*)"],
};
