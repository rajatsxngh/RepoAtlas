import { expect, test } from "@playwright/test";
import { mapNode, waitForMap } from "../helpers";

const ADD = "py:src/shop/cart.py:Cart.add:method";
const GIFT_ADD = "py:src/shop/cart.py:GiftCart.add:method";

test("What breaks? against the real API: ranked groups, scored layer, evidence", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/shop");
  await page.getByRole("button", { name: /Map it/ }).click();
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  await waitForMap(page);

  await page.getByPlaceholder("Search functions, classes, files…").fill("Cart.add");
  await page
    .getByRole("option")
    .filter({ hasText: "src/shop/cart.py" })
    .filter({ hasNotText: "GiftCart" })
    .first()
    .click();
  await page
    .getByRole("complementary", { name: "Node details" })
    .getByRole("button", { name: "What breaks?" })
    .click();

  const panel = page.getByRole("complementary", { name: "What breaks" });
  const giftAdd = panel.getByRole("region", { name: "Callers" }).getByTitle(GIFT_ADD);
  await expect(giftAdd).toContainText("src/shop/cart.py · direct");
  await expect(panel.getByRole("region", { name: "Tests" })).toContainText(
    "test_add_rounds_prices",
  );
  await expect(panel.getByRole("region", { name: "Unknown" })).toBeVisible();

  await expect(mapNode(page, GIFT_ADD).locator('[data-highlight="warning"]')).toHaveCount(1);
  await expect(mapNode(page, ADD).locator('[data-highlight="primary"]')).toHaveCount(1);

  await giftAdd.click();
  const viewer = panel.getByRole("figure");
  await expect(viewer).toContainText("add");
  await expect(viewer.locator(".ra-code-mark").first()).toBeVisible();
});
