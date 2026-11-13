import { expect, test } from "@playwright/test";
import { failOnDialogs, mapNode, waitForMap } from "../helpers";

const ROUND_MONEY = "py:src/shop/utils/helpers.py:round_money:function";
const ADD = "py:src/shop/cart.py:Cart.add:method";

test("What breaks? lists ranked tests and callers, highlights them, and opens evidence", async ({
  page,
}) => {
  const dialogs = failOnDialogs(page);
  await page.goto("/s/1");
  await waitForMap(page);

  await page.getByPlaceholder("Search functions, classes, files…").fill("round_money");
  await page
    .getByRole("option", { name: /round_money/ })
    .first()
    .click();
  const details = page.getByRole("complementary", { name: "Node details" });
  await details.getByRole("button", { name: "What breaks?" }).click();

  const panel = page.getByRole("complementary", { name: "What breaks" });
  await expect(panel).toContainText("What breaks if round_money changes?");
  const callers = panel.getByRole("region", { name: "Callers" });
  const add = callers.getByTitle(ADD);
  await expect(add).toContainText("src/shop/cart.py · direct");
  await expect(
    panel.getByRole("region", { name: "Tests" }).getByRole("button").first(),
  ).toBeVisible();
  await expect(panel.getByTestId("impact-stats")).toContainText("affected");

  // The affected symbols are revealed and drawn with the scored warning layer.
  await expect(mapNode(page, ADD)).toBeVisible();
  await expect(mapNode(page, ADD).locator('[data-highlight="warning"]')).toHaveCount(1);
  await expect(mapNode(page, ROUND_MONEY).locator('[data-highlight="primary"]')).toHaveCount(1);

  // An item's evidence opens in the code viewer.
  await add.click();
  await expect(add).toHaveAttribute("aria-pressed", "true");
  const viewer = panel.getByRole("figure");
  await expect(viewer).toContainText("round_money");
  await expect(viewer.locator(".ra-code-mark").first()).toBeVisible();

  // Closing the panel removes the layer.
  await panel.getByRole("button", { name: "Close impact" }).click();
  await expect(panel).toHaveCount(0);
  await expect(mapNode(page, ADD).locator('[data-highlight="warning"]')).toHaveCount(0);
  expect(dialogs).toEqual([]);
});
