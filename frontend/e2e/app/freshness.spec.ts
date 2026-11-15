import { expect, test } from "@playwright/test";
import { mapNode, openNode, waitForMap } from "../helpers";

/**
 * The stack's `drift-*` repositories are the shop with a default branch that moves right after
 * the first build (backend/tests/e2e/fixtures.py): the first map is three commits behind. A
 * name nobody used starts from the first commit, so each attempt takes its own.
 */
test("a map behind its repository says so, and the rebuild opens a current map", async ({
  page,
}, testInfo) => {
  const repo = `repoatlas-e2e/drift-${Date.now()}-${testInfo.retry}`;
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill(`https://github.com/${repo}`);
  await page.getByRole("button", { name: /Map it/ }).click();
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  const staleUrl = page.url();
  await waitForMap(page);

  // The API asked the repository (git ls-remote) and counted the commits in between.
  const banner = page.getByRole("region", { name: "Map freshness" });
  await expect(banner).toContainText("This map is 3 commits behind main");
  // The first commit has no tax module yet.
  await openNode(page, "dir:src");
  await openNode(page, "dir:src/shop");
  await expect(mapNode(page, "file:src/shop/cart.py")).toBeVisible();
  await expect(mapNode(page, "file:src/shop/tax.py")).toHaveCount(0);

  await banner.getByRole("button", { name: "Rebuild" }).click();
  await expect(banner.getByRole("status")).toBeVisible();
  await page.waitForURL((url) => /\/s\/\d+$/.test(url.pathname) && url.href !== staleUrl, {
    timeout: 60_000,
  });
  await waitForMap(page);

  // The new map is the head: no banner, and the module the later commits added.
  await expect(page.getByRole("region", { name: "Map freshness" })).toHaveCount(0);
  await openNode(page, "dir:src");
  await openNode(page, "dir:src/shop");
  await expect(mapNode(page, "file:src/shop/tax.py")).toBeVisible();

  // The old map is still there, and still says it is behind.
  await page.goto(staleUrl);
  await waitForMap(page);
  await expect(page.getByRole("region", { name: "Map freshness" })).toContainText(
    "3 commits behind",
  );
});
