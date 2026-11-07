import { describe, expect, it } from "vitest";
import { checkRepoUrl } from "./repoUrl";

describe("checkRepoUrl", () => {
  it.each([
    ["https://github.com/pallets/flask", "https://github.com/pallets/flask"],
    ["  https://github.com/pallets/flask/  ", "https://github.com/pallets/flask"],
    ["https://github.com/pallets/flask.git", "https://github.com/pallets/flask"],
    ["HTTPS://GitHub.com/colinhacks/zod", "https://github.com/colinhacks/zod"],
    ["https://github.com/a-b/c.d_e-f", "https://github.com/a-b/c.d_e-f"],
  ])("accepts %j", (input, url) => {
    expect(checkRepoUrl(input)).toMatchObject({ ok: true, url });
  });

  it.each([
    ["", "Paste the URL"],
    ["http://github.com/pallets/flask", "https://"],
    ["https://gitlab.com/pallets/flask", "Only https://github.com"],
    ["https://github.com:443/pallets/flask", "Only https://github.com"],
    ["https://user:pw@github.com/pallets/flask", "credentials"],
    ["https://github.com/pallets/flask?tab=readme", "query string"],
    ["https://github.com/pallets/flask#readme", "query string"],
    ["https://github.com/pallets", "Expected"],
    ["https://github.com/pallets/flask/tree/main", "Expected"],
    ["https://github.com/-bad/flask", "owner"],
    ["https://github.com/pallets/..", "repository name"],
    ["https://github.com/pallets/fl ask", "repository name"],
    ["javascript:alert(1)", "Expected"],
  ])("rejects %j", (input, message) => {
    const result = checkRepoUrl(input);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain(message);
  });

  it.each([
    ["pallets/flask", "https://github.com/pallets/flask"],
    ["github.com/pallets/flask", "https://github.com/pallets/flask"],
    ["http://github.com/pallets/flask", "https://github.com/pallets/flask"],
    ["https://www.github.com/pallets/flask", "https://github.com/pallets/flask"],
    ["https://github.com/pallets/flask/tree/main", "https://github.com/pallets/flask"],
    ["https://github.com/pallets/flask?tab=readme", "https://github.com/pallets/flask"],
  ])("suggests a fix for %j", (input, suggestion) => {
    expect(checkRepoUrl(input)).toMatchObject({ ok: false, suggestion });
  });
});
