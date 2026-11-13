/** Liveness of the web server (open even when the access gate is on; says nothing else). */

export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({ status: "ok" }, { headers: { "Cache-Control": "no-store" } });
}
