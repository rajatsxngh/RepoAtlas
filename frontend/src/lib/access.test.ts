import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as enter } from "@/app/access/enter/route";
import { proxy } from "@/proxy";
import {
  ACCESS_COOKIE,
  ACCESS_MAX_AGE_S,
  accessCookie,
  accessToken,
  cookieOpens,
  cookieValue,
  isOpenPath,
  isSecureRequest,
  safeEqual,
  safeNext,
} from "./access";

const TOKEN = "s3cret-token-for-tests";
const SITE = "http://web.test";

afterEach(() => vi.unstubAllEnvs());

describe("the access cookie", () => {
  it("holds a digest of the token, the same one the API computes", async () => {
    const value = await cookieValue(TOKEN);
    // sha256("repoatlas-access:" + token), as in repoatlas.api.access.cookie_value
    expect(value).toMatch(/^[0-9a-f]{64}$/);
    expect(value).not.toContain(TOKEN);
    // The same fixed case is in backend/tests/unit/api/test_access.py.
    expect(await cookieValue("abc")).toBe(
      "2d23b51f143f219a1ce8f7824c551b180de4fa2916aecf6e4af0898bae6e8c89",
    );
    expect(await cookieOpens(value, TOKEN)).toBe(true);
    expect(await cookieOpens(TOKEN, TOKEN)).toBe(false);
    expect(await cookieOpens(undefined, TOKEN)).toBe(false);
    expect(await cookieOpens(await cookieValue(`${TOKEN}x`), TOKEN)).toBe(false);
  });

  it("is HTTP-only, same-site, and secure on HTTPS", () => {
    const plain = accessCookie("v", false);
    expect(plain).toBe(
      `${ACCESS_COOKIE}=v; Path=/; Max-Age=${ACCESS_MAX_AGE_S}; HttpOnly; SameSite=Lax`,
    );
    expect(accessCookie("v", true)).toBe(`${plain}; Secure`);
    expect(isSecureRequest(new Request("https://web.test/"))).toBe(true);
    expect(isSecureRequest(new Request("http://web.test/"))).toBe(false);
    const behindProxy = new Request("http://10.0.0.5:3000/", {
      headers: { "x-forwarded-proto": "https, http" },
    });
    expect(isSecureRequest(behindProxy)).toBe(true);
  });
});

describe("safeEqual", () => {
  it.each([
    ["abc", "abc", true],
    ["abc", "abd", false],
    ["abc", "abcd", false],
    ["abcd", "abc", false],
    ["", "abc", false],
    ["", "", true],
    ["tökén", "tökén", true],
  ])("compares %j with %j", (given, expected, same) => {
    expect(safeEqual(given, expected)).toBe(same);
  });
});

describe("safeNext", () => {
  it.each([
    ["/s/12?node=a%2Fb", "/s/12?node=a%2Fb"],
    ["/", "/"],
    [null, "/"],
    ["", "/"],
    ["https://evil.test/", "/"],
    ["//evil.test/", "/"],
    ["/\\evil.test", "/"],
    ["javascript:alert(1)", "/"],
    ["/ok\nSet-Cookie: x=1", "/"],
    ["/access", "/"],
    ["/access/enter", "/"],
    ["/access?next=/access", "/"],
    [`/${"a".repeat(2001)}`, "/"],
  ])("maps %j to %j", (next, expected) => {
    expect(safeNext(next)).toBe(expected);
  });
});

describe("settings", () => {
  it("has no gate without a token", () => {
    expect(accessToken({})).toBeNull();
    expect(accessToken({ REPOATLAS_ACCESS_TOKEN: "  " })).toBeNull();
    expect(accessToken({ REPOATLAS_ACCESS_TOKEN: ` ${TOKEN} ` })).toBe(TOKEN);
  });

  it("leaves only the token page, its form target, and liveness open", () => {
    expect(["/access", "/access/enter", "/healthz"].every(isOpenPath)).toBe(true);
    expect(["/", "/s/1", "/api/v1/ready", "/accessx", "/healthz/x"].some(isOpenPath)).toBe(false);
  });
});

function visit(path: string, cookie?: string): NextRequest {
  const headers = new Headers();
  if (cookie) headers.set("cookie", `${ACCESS_COOKIE}=${cookie}`);
  return new NextRequest(`${SITE}${path}`, { headers });
}

describe("the gate (proxy.ts)", () => {
  it("lets everything through when no token is set", async () => {
    const response = await proxy(visit("/s/1"));
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("sends a visitor without the cookie to the token page, remembering where they were", async () => {
    vi.stubEnv("REPOATLAS_ACCESS_TOKEN", TOKEN);
    const page = await proxy(visit("/s/12?node=a%2Fb"));
    expect(page.status).toBe(307);
    expect(page.headers.get("location")).toBe(`${SITE}/access?next=%2Fs%2F12%3Fnode%3Da%252Fb`);
    expect((await proxy(visit("/"))).headers.get("location")).toBe(`${SITE}/access`);
    expect((await proxy(visit("/s/1", "wrong"))).status).toBe(307);
    expect((await proxy(visit("/s/1", TOKEN))).status).toBe(307); // the token is not the cookie
  });

  it("answers API calls without the cookie with 401 in the error model", async () => {
    vi.stubEnv("REPOATLAS_ACCESS_TOKEN", TOKEN);
    const response = await proxy(visit("/api/v1/snapshots/1"));
    expect(response.status).toBe(401);
    const body = await response.json();
    expect(body.error.code).toBe("unauthorized");
    expect(JSON.stringify(body)).not.toContain(TOKEN);
  });

  it("lets a script call the API with the token as a Bearer header, but not open pages", async () => {
    vi.stubEnv("REPOATLAS_ACCESS_TOKEN", TOKEN);
    const bearer = (path: string, value: string) =>
      new NextRequest(`${SITE}${path}`, { headers: { authorization: `Bearer ${value}` } });
    expect((await proxy(bearer("/api/v1/ready", TOKEN))).headers.get("x-middleware-next")).toBe(
      "1",
    );
    expect((await proxy(bearer("/api/v1/ready", "guess"))).status).toBe(401);
    expect((await proxy(bearer("/s/1", TOKEN))).status).toBe(307);
  });

  it("opens with the cookie, and keeps the token page and liveness open without it", async () => {
    vi.stubEnv("REPOATLAS_ACCESS_TOKEN", TOKEN);
    const cookie = await cookieValue(TOKEN);
    for (const request of [
      visit("/s/1", cookie),
      visit("/api/v1/ready", cookie),
      visit("/access"),
      visit("/healthz"),
    ]) {
      expect((await proxy(request)).headers.get("x-middleware-next")).toBe("1");
    }
  });
});

function submit(fields: Record<string, string>, init: RequestInit = {}): Request {
  return new Request(`${SITE}/access/enter`, {
    method: "POST",
    body: new URLSearchParams(fields),
    ...init,
  });
}

describe("entering the token", () => {
  it("sets the cookie and goes on to where the visitor was headed", async () => {
    vi.stubEnv("REPOATLAS_ACCESS_TOKEN", TOKEN);
    const response = await enter(submit({ token: ` ${TOKEN} `, next: "/s/12" }));
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/s/12");
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`${ACCESS_COOKIE}=${await cookieValue(TOKEN)}`);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).not.toContain(TOKEN);
    expect(cookie).not.toContain("Secure"); // plain HTTP here
    const https = await enter(
      submit({ token: TOKEN }, { headers: { "x-forwarded-proto": "https" } }),
    );
    expect(https.headers.get("set-cookie")).toContain("Secure");
    expect(https.headers.get("location")).toBe("/");
  });

  it("rejects a wrong token without a cookie and never redirects off the site", async () => {
    vi.stubEnv("REPOATLAS_ACCESS_TOKEN", TOKEN);
    const wrong = await enter(submit({ token: "guess", next: "/s/12" }));
    expect(wrong.status).toBe(303);
    expect(wrong.headers.get("location")).toBe("/access?error=1&next=%2Fs%2F12");
    expect(wrong.headers.get("set-cookie")).toBeNull();
    const empty = await enter(submit({}));
    expect(empty.headers.get("location")).toBe("/access?error=1");
    const away = await enter(submit({ token: TOKEN, next: "https://evil.test/" }));
    expect(away.headers.get("location")).toBe("/");
    const notAForm = await enter(
      new Request(`${SITE}/access/enter`, {
        method: "POST",
        body: "{}",
        headers: { "content-type": "application/json" },
      }),
    );
    expect(notAForm.headers.get("set-cookie")).toBeNull();
  });

  it("does nothing when the deployment has no gate", async () => {
    const response = await enter(submit({ token: "anything" }));
    expect(response.headers.get("location")).toBe("/");
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});
