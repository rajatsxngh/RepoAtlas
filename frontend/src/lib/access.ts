/**
 * The private access gate, web side (the API's side is `repoatlas.api.access`).
 *
 * When the server has `REPOATLAS_ACCESS_TOKEN`, every page and API call needs the access cookie.
 * A visitor types the token once on `/access`; the server answers with an HTTP-only cookie that
 * holds a digest of the token (never the token itself), and `src/proxy.ts` checks that cookie on
 * every later request. Without the variable there is no gate (local development).
 *
 * Everything here runs on the server only. The token is never logged and never sent to the
 * browser.
 */

export const ACCESS_COOKIE = "repoatlas_access";
export const ACCESS_PATH = "/access";
export const ACCESS_ENTER_PATH = "/access/enter";
export const ACCESS_MAX_AGE_S = 30 * 24 * 3600;
const COOKIE_CONTEXT = "repoatlas-access:";

/** The configured token, or `null` when the deployment has no gate. */
export function accessToken(env: Record<string, string | undefined> = process.env): string | null {
  const token = env.REPOATLAS_ACCESS_TOKEN?.trim();
  return token ? token : null;
}

/** What the access cookie holds for `token`: sha256 of a fixed prefix and the token, in hex.
 * The API computes the same value (`repoatlas.api.access.cookie_value`). */
export async function cookieValue(token: string): Promise<string> {
  const bytes = new TextEncoder().encode(COOKIE_CONTEXT + token);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Compare two strings in time that depends only on their lengths, not on where they differ. */
export function safeEqual(given: string, expected: string): boolean {
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(expected);
  let difference = a.length ^ b.length;
  for (let index = 0; index < b.length; index += 1) {
    difference |= (a[index % Math.max(a.length, 1)] ?? 0) ^ (b[index] ?? 0);
  }
  return difference === 0;
}

/** Whether `cookie` is the access cookie for `token`. */
export async function cookieOpens(cookie: string | undefined, token: string): Promise<boolean> {
  return cookie !== undefined && safeEqual(cookie, await cookieValue(token));
}

/**
 * Where to send a visitor after the token is accepted: `next` when it is a path on this site,
 * else the home page. Anything that could leave the site (another origin, `//host`, a
 * backslash some browsers read as a slash, control characters) is refused.
 */
export function safeNext(next: string | null | undefined): string {
  if (!next || next.length > 2000) return "/";
  if (!next.startsWith("/") || next.startsWith("//")) return "/";
  if (/[\\\u0000-\u001f\u007f]/.test(next)) return "/";
  if (next === ACCESS_PATH || next.startsWith(`${ACCESS_PATH}/`) || next.startsWith("/access?")) {
    return "/";
  }
  return next;
}

/** Paths the gate leaves open: the token page, its form target, and the liveness probe. */
export function isOpenPath(pathname: string): boolean {
  return pathname === ACCESS_PATH || pathname === ACCESS_ENTER_PATH || pathname === "/healthz";
}

/** The `Set-Cookie` attributes of the access cookie. `secure` follows the visitor's scheme. */
export function accessCookie(value: string, secure: boolean): string {
  const parts = [
    `${ACCESS_COOKIE}=${value}`,
    "Path=/",
    `Max-Age=${ACCESS_MAX_AGE_S}`,
    "HttpOnly",
    "SameSite=Lax",
  ];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

/** Whether the visitor's connection is HTTPS (directly, or as a reverse proxy reports it). */
export function isSecureRequest(request: Request): boolean {
  const forwarded = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  if (forwarded) return forwarded === "https";
  return new URL(request.url).protocol === "https:";
}
