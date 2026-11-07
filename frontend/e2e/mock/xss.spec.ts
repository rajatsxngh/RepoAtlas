import { expect, test } from "@playwright/test";
import { failOnDialogs, injectedElements, mapNode, openNode, waitForMap } from "../helpers";

const DIR = "<img src=x onerror=alert('dir')>";
const FILE = "<svg onload=alert('file')>.py";

test("the mock XSS fixture renders as text in map labels, the panel, and the code viewer", async ({
  page,
}) => {
  const dialogs = failOnDialogs(page);
  await page.goto("/s/3");
  await waitForMap(page);
  await expect(page.locator(".react-flow").getByText(DIR, { exact: true })).toBeVisible();
  await openNode(page, `dir:${DIR}`);
  const fileKey = `file:${DIR}/${FILE}`;
  await openNode(page, fileKey);
  await mapNode(page, fileKey).locator(".ra-container-header").click();
  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByRole("heading", { name: FILE })).toBeVisible();
  await expect(panel.getByRole("figure")).toContainText("# <script>alert('comment')</script>");
  expect(dialogs).toEqual([]);
  expect(await injectedElements(page)).toBe(0);
});
