import { expect, test } from "@playwright/test";
import { waitForMap } from "../helpers";

test("the landing page lists precomputed repositories", async ({ page }) => {
  await page.goto("/");
  const gallery = page.getByRole("region", { name: "Or open one that is already mapped" });
  await expect(gallery.getByRole("listitem")).toHaveCount(2);
  await expect(gallery.getByRole("listitem").first()).toContainText("Shop (Python)");
  await expect(gallery.getByRole("listitem").first()).toContainText("In the benchmark");
  await expect(gallery.getByRole("listitem").nth(1)).toContainText("repoatlas-fixtures/ts-small");
});

test("a gallery card opens its map without a build", async ({ page }) => {
  await page.goto("/");
  const gallery = page.getByRole("region", { name: "Or open one that is already mapped" });
  await gallery.getByRole("link", { name: "Open the map of repoatlas-fixtures/ts-small" }).click();

  await page.waitForURL("**/s/2");
  // No build progress on the way: the snapshot was ready before the click.
  await expect(page.getByRole("region", { name: "Building the map" })).toHaveCount(0);
  await waitForMap(page);
  await expect(page.getByText("repoatlas-fixtures/ts-small")).toBeVisible();
});
