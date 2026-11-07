import { expect, test } from "@playwright/test";
import { mapNode, openNode, waitForMap } from "../helpers";

const ADD = "py:src/shop/cart.py:Cart.add:method";
const ROUND_MONEY = "py:src/shop/utils/helpers.py:round_money:function";

test("submit a URL, follow the build, explore the map, read code, and highlight a path", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/shop");
  await page.getByRole("button", { name: /Map it/ }).click();

  // Progress with stage names, then the map.
  const progress = page.getByRole("region", { name: "Building the map" });
  await expect(progress).toBeVisible();
  await expect(progress.getByText("repoatlas-e2e/shop")).toBeVisible();
  await expect(progress.getByRole("list", { name: "Build stages" })).toContainText(
    "Fetching the source from GitHub",
  );
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  await waitForMap(page);
  for (const folder of ["src", "tests"]) {
    await expect(mapNode(page, `dir:${folder}`)).toBeVisible();
  }

  // Expand folders, a file, and a class.
  for (const key of [
    "dir:src",
    "dir:src/shop",
    "file:src/shop/cart.py",
    "py:src/shop/cart.py:Cart:class",
  ]) {
    await openNode(page, key);
  }
  await expect(mapNode(page, ADD)).toBeVisible();

  // The node panel, with owners and code.
  await mapNode(page, ADD).click();
  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByRole("heading", { name: "add" })).toBeVisible();
  await expect(panel).toContainText("src/shop/cart.py · lines 14–15");
  await expect(panel).toContainText("Avery Chen");
  await expect(panel.getByRole("figure")).toContainText("def add(self, name: str, price: Decimal)");

  // A path, highlighted on the map with every node on it revealed.
  await page.getByRole("button", { name: "Path", exact: true }).click();
  const finder = page.getByRole("region", { name: "Path finder" });
  await finder.getByRole("combobox", { name: "Path from" }).fill("test_add_rounds");
  await page.getByRole("option", { name: /test_add_rounds_prices/ }).click();
  await finder.getByRole("combobox", { name: "Path to" }).fill("round_money");
  await page.getByRole("option", { name: /round_money/ }).click();
  const steps = finder.getByRole("list", { name: "Path steps" });
  await expect(steps).toContainText("test_add_rounds_prices");
  await expect(steps).toContainText("round_money");
  await expect(mapNode(page, ROUND_MONEY)).toBeVisible();
  await expect(mapNode(page, "py:tests/test_cart.py:test_add_rounds_prices:test")).toBeVisible();
  await expect(page.locator('path[data-highlight="primary"]').first()).toBeVisible();
  await expect(mapNode(page, ROUND_MONEY).locator('[data-highlight="primary"]')).toHaveCount(1);
});

test("explains a repository it can't map", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/private");
  await page.getByRole("button", { name: /Map it/ }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "This repository is private" }),
  ).toBeVisible();
});
