import { expect, test } from "@playwright/test";
import { waitForMap } from "../helpers";

// Mock mode's snapshot 6 is py-small three commits ago; snapshot 1 is its head.
const STALE = "/s/6";

test("a stale map says how far behind it is, and rebuilding opens a current map", async ({
  page,
}) => {
  await page.goto(STALE);
  await waitForMap(page);
  const banner = page.getByRole("region", { name: "Map freshness" });
  await expect(banner).toContainText("This map is 3 commits behind main");
  await expect(banner).toContainText("built from 1a2b3c4, now at 3f9d2c7");

  await banner.getByRole("button", { name: "Rebuild" }).click();
  await expect(banner.getByRole("status")).toBeVisible();
  await expect(banner.getByRole("button", { name: "Rebuild" })).toHaveCount(0);

  // The build finishes and the new map opens: the head's commit, and no banner.
  await page.waitForURL("**/s/1");
  await waitForMap(page);
  await expect(page.getByText("3f9d2c7", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Map freshness" })).toHaveCount(0);
});

test("a map built from the head shows no banner", async ({ page }) => {
  await page.goto("/s/2");
  await waitForMap(page);
  await expect(page.getByText("repoatlas-fixtures/ts-small")).toBeVisible();
  await expect(page.getByRole("region", { name: "Map freshness" })).toHaveCount(0);
});

test("the banner can be dismissed and leaves the map usable", async ({ page }) => {
  await page.goto(STALE);
  await waitForMap(page);
  const banner = page.getByRole("region", { name: "Map freshness" });
  await expect(banner).toBeVisible();
  await banner.getByRole("button", { name: "Dismiss" }).click();
  await expect(banner).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Search symbols" })).toBeVisible();
});
