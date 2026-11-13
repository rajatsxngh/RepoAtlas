import { expect, test } from "@playwright/test";
import { mapNode, waitForMap } from "../helpers";

const ROUND_MONEY = "py:src/shop/utils/helpers.py:round_money:function";
const ADD = "py:src/shop/cart.py:Cart.add:method";

test("What breaks? against the real API: ranked groups, scored layer, evidence", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/shop");
  await page.getByRole("button", { name: /Map it/ }).click();
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  await waitForMap(page);

  await page.getByPlaceholder("Search functions, classes, files…").fill("round_money");
  await page
    .getByRole("option", { name: /round_money/ })
    .first()
    .click();
  await page
    .getByRole("complementary", { name: "Node details" })
    .getByRole("button", { name: "What breaks?" })
    .click();

  const panel = page.getByRole("complementary", { name: "What breaks" });
  const add = panel.getByRole("region", { name: "Callers" }).getByTitle(ADD);
  await expect(add).toContainText("src/shop/cart.py · direct");
  const tests = panel.getByRole("region", { name: "Tests" });
  await expect(tests).toContainText("test_add_rounds_prices");
  await expect(panel.getByRole("region", { name: "Unknown" })).toBeVisible();

  await expect(mapNode(page, ADD).locator('[data-highlight="warning"]')).toHaveCount(1);
  await expect(mapNode(page, ROUND_MONEY).locator('[data-highlight="primary"]')).toHaveCount(1);

  await add.click();
  const viewer = panel.getByRole("figure");
  await expect(viewer).toContainText("round_money");
  await expect(viewer.locator(".ra-code-mark").first()).toBeVisible();
});
