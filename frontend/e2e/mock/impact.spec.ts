import { expect, test, type Page } from "@playwright/test";
import { failOnDialogs, mapNode, waitForMap } from "../helpers";

const ADD = "py:src/shop/cart.py:Cart.add:method";
const GIFT_ADD = "py:src/shop/cart.py:GiftCart.add:method";

async function whatBreaksOfCartAdd(page: Page) {
  await page.getByPlaceholder("Search functions, classes, files…").fill("Cart.add");
  await page
    .getByRole("option")
    .filter({ hasText: "src/shop/cart.py" })
    .filter({ hasNotText: "GiftCart" })
    .first()
    .click();
  const details = page.getByRole("complementary", { name: "Node details" });
  await details.getByRole("button", { name: "What breaks?" }).click();
}

test("What breaks? lists ranked tests and callers, highlights them, and opens evidence", async ({
  page,
}) => {
  const dialogs = failOnDialogs(page);
  await page.goto("/s/1");
  await waitForMap(page);
  await whatBreaksOfCartAdd(page);

  const panel = page.getByRole("complementary", { name: "What breaks" });
  await expect(panel).toContainText("What breaks if Cart.add changes?");
  const giftAdd = panel.getByRole("region", { name: "Callers" }).getByTitle(GIFT_ADD);
  await expect(giftAdd).toContainText("src/shop/cart.py · direct");
  await expect(panel.getByRole("region", { name: "Tests" })).toContainText(
    "test_add_rounds_prices",
  );
  await expect(panel.getByTestId("impact-stats")).toContainText("affected");

  // The affected symbols are revealed and drawn with the scored warning layer.
  await expect(mapNode(page, GIFT_ADD)).toBeVisible();
  await expect(mapNode(page, GIFT_ADD).locator('[data-highlight="warning"]')).toHaveCount(1);
  await expect(mapNode(page, ADD).locator('[data-highlight="primary"]')).toHaveCount(1);

  // An item's evidence opens in the code viewer.
  await giftAdd.click();
  await expect(giftAdd).toHaveAttribute("aria-pressed", "true");
  const viewer = panel.getByRole("figure");
  await expect(viewer).toContainText("add");
  await expect(viewer.locator(".ra-code-mark").first()).toBeVisible();

  // Closing the panel removes the layer.
  await panel.getByRole("button", { name: "Close impact" }).click();
  await expect(panel).toHaveCount(0);
  await expect(mapNode(page, GIFT_ADD).locator('[data-highlight="warning"]')).toHaveCount(0);
  expect(dialogs).toEqual([]);
});
