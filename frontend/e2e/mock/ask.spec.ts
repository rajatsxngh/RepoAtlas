import { expect, test } from "@playwright/test";
import { failOnDialogs, mapNode, waitForMap } from "../helpers";

const CREATE_APP = "py:src/shop/app.py:create_app:function";

test("asking shows a cited answer; a citation opens the code and highlights the map", async ({
  page,
}) => {
  const dialogs = failOnDialogs(page);
  await page.goto("/s/1");
  await waitForMap(page);

  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel.getByRole("textbox", { name: "Question" }).fill("How does create_app work?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();

  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("create_app");
  await expect(answer.getByTestId("answer-metrics")).toContainText(
    "1 of 1 citations valid before filtering",
  );

  const chip = answer.getByRole("list", { name: "Claims" }).getByRole("button", {
    name: "app.py:22–23",
  });
  await chip.click();
  await expect(chip).toHaveAttribute("aria-pressed", "true");

  // The cited lines open in the code viewer, marked.
  const viewer = panel.getByRole("figure");
  await expect(viewer).toContainText("def create_app");
  await expect(viewer.locator(".ra-code-mark")).toHaveCount(2);
  await expect(viewer.locator('.ra-code-mark[data-line="22"]')).toBeVisible();

  // The cited symbol is revealed on the map and highlighted.
  await expect(mapNode(page, CREATE_APP)).toBeVisible();
  await expect(mapNode(page, CREATE_APP).locator('[data-highlight="primary"]')).toHaveCount(1);

  expect(dialogs).toEqual([]);
});

test("a question the code can't answer is refused for lack of evidence", async ({ page }) => {
  await page.goto("/s/1");
  await waitForMap(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel.getByRole("textbox", { name: "Question" }).fill("How are refunds sent to the bank?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("Not enough evidence");
  await expect(answer.getByRole("list", { name: "Claims" })).toHaveCount(0);
});
