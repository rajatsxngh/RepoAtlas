/** The token form's target: check the token, set the access cookie, and go on. */

import {
  ACCESS_PATH,
  accessCookie,
  accessToken,
  cookieValue,
  isSecureRequest,
  safeEqual,
  safeNext,
} from "@/lib/access";

export const dynamic = "force-dynamic";

function redirect(location: string, cookie?: string): Response {
  const headers = new Headers({ Location: location, "Cache-Control": "no-store" });
  if (cookie) headers.set("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}

export async function POST(request: Request): Promise<Response> {
  const token = accessToken();
  if (token === null) return redirect("/");
  let given = "";
  let next = "/";
  try {
    const form = await request.formData();
    const field = form.get("token");
    given = typeof field === "string" ? field.trim() : "";
    const target = form.get("next");
    next = safeNext(typeof target === "string" ? target : null);
  } catch {
    // Not a form: treated as a wrong token below.
  }
  if (!safeEqual(given, token)) {
    const query = new URLSearchParams({ error: "1" });
    if (next !== "/") query.set("next", next);
    return redirect(`${ACCESS_PATH}?${query.toString()}`);
  }
  return redirect(next, accessCookie(await cookieValue(token), isSecureRequest(request)));
}
