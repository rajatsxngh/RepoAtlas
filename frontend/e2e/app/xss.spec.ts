import { expect, test } from "@playwright/test";
import { failOnDialogs, injectedElements, mapNode, openNode, waitForMap } from "../helpers";

// Names and text from backend/tests/e2e/fixtures.py (the "xss" repository).
const DIR = "<img src=x onerror=alert('dir')>";
const FILE = "<svg onload=alert('file')>.py";
const TS_FILE = "\"><svg onload=alert('svg')>.ts";
const TEST_FILE = "<svg onload=alert('spec')>.test.ts";
const OWNER = "&lt;img src=x onerror=alert('owner')&gt; & \"Ra\" Lima";

test("HTML and script in names, code, and authors render as inert text", async ({ page }) => {
  const dialogs = failOnDialogs(page);
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/xss");
  await page.getByRole("button", { name: /Map it/ }).click();
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  await waitForMap(page);

  // Map labels.
  for (const name of [DIR, TS_FILE, TEST_FILE]) {
    await expect(page.locator(".react-flow").getByText(name, { exact: true })).toBeVisible();
  }
  await openNode(page, `dir:${DIR}`);
  const fileKey = `file:${DIR}/${FILE}`;
  await expect(mapNode(page, fileKey)).toContainText(FILE);
  await openNode(page, fileKey);
  await expect(page.locator(".react-flow")).toContainText("GET /<script>alert('route')</script>");
  await openNode(page, `file:${TEST_FILE}`);
  await expect(page.locator(".react-flow")).toContainText("<script>alert('test')</script> renders");

  // Panel and code viewer.
  await mapNode(page, fileKey).locator(".ra-container-header").click();
  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByRole("heading", { name: FILE })).toBeVisible();
  await expect(panel.getByText(OWNER, { exact: true })).toBeVisible();
  const code = panel.getByRole("figure");
  await expect(code).toContainText("# <script>alert('comment')</script>");
  await expect(code).toContainText(`return helper("</textarea><script>alert('string')</script>")`);
  await expect(code).toContainText("&lt;not an entity&gt; &amp; <!-- comment -->");

  // Search results.
  await page.getByRole("combobox", { name: "Search symbols" }).fill("render");
  await expect(page.getByRole("option", { name: /render/ }).first()).toBeVisible();

  expect(dialogs).toEqual([]);
  expect(await injectedElements(page)).toBe(0);
});
