import { expect, test } from "@playwright/test";
import { mapNode, waitForMap } from "../helpers";

// The stack precomputes repoatlas-e2e/gallery before the API starts
// (backend/tests/e2e/stack.py), the way `repoatlas precompute` does on a deployment.

test("a precomputed repository opens from the landing page without a build", async ({ page }) => {
  const builds: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/repos")) builds.push(request.url());
  });

  await page.goto("/");
  const gallery = page.getByRole("region", { name: "Or open one that is already mapped" });
  const card = gallery.getByRole("listitem").filter({ hasText: "repoatlas-e2e/gallery" });
  await expect(card).toContainText("Shop");
  await expect(card).toContainText("A small shop: a cart, a catalog, and their tests.");
  await expect(card).toContainText("In the benchmark");

  await card.getByRole("link", { name: "Open the map of repoatlas-e2e/gallery" }).click();
  await page.waitForURL(/\/s\/\d+$/);
  await expect(page.getByRole("region", { name: "Building the map" })).toHaveCount(0);
  await waitForMap(page);
  for (const folder of ["src", "tests"]) {
    await expect(mapNode(page, `dir:${folder}`)).toBeVisible();
  }
  expect(builds).toEqual([]); // nothing was built to open it
});
