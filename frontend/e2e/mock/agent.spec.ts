import { expect, test } from "@playwright/test";
import { failOnDialogs, mapNode, waitForMap } from "../helpers";

const CREATE_APP = "py:src/shop/app.py:create_app:function";

test("agent mode shows its tool calls, a cited answer, and a trace with the tool steps", async ({
  page,
}) => {
  const dialogs = failOnDialogs(page);
  await page.goto("/s/1");
  await waitForMap(page);

  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  const modes = panel.getByRole("radiogroup", { name: "Answer mode" });
  await expect(modes.getByRole("radio", { name: "Quick" })).toBeChecked();
  // The radio itself is visually hidden; its label is what a person clicks.
  await modes.getByText("Agent", { exact: true }).click();
  await expect(modes.getByRole("radio", { name: "Agent", exact: true })).toBeChecked();
  await expect(panel.getByTestId("ask-mode-hint")).toContainText("Explores the code with tools");

  await panel.getByRole("textbox", { name: "Question" }).fill("How does create_app work?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();

  // The steps: three tool calls, the end of the exploration, the final answer call.
  const steps = panel.getByRole("region", { name: "Agent steps" });
  const rows = steps.getByRole("listitem");
  await expect(rows).toHaveCount(5);
  await expect(rows.nth(0)).toContainText("search_code");
  await expect(rows.nth(0)).toContainText('query="How does create_app work?"');
  await expect(rows.nth(0)).toContainText("5 pieces of evidence");
  await expect(rows.nth(1)).toContainText("find_definition");
  await expect(rows.nth(2)).toContainText("read_file");
  await expect(rows.nth(3)).toContainText("Finished exploring");
  await expect(rows.nth(4)).toContainText("Wrote the answer");
  await expect(rows.nth(4)).toContainText("claude-sonnet-5-5");
  await expect(steps.getByTestId("steps-summary")).toContainText("3 tool calls · $0.0133");
  await expect(steps).toHaveAttribute("aria-busy", "false");

  // The answer is cited like any other, and costs what its steps cost.
  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("create_app");
  const badge = answer.getByTestId("cost-badge");
  await expect(badge).toContainText("$0.0133");
  const chip = answer.getByRole("list", { name: "Claims" }).getByRole("button", {
    name: "app.py:22–23",
  });
  await chip.click();
  await expect(panel.getByRole("figure")).toContainText("def create_app");
  await expect(mapNode(page, CREATE_APP).locator('[data-highlight="primary"]')).toHaveCount(1);

  // Its trace has the model turns with their tool calls under them.
  await badge.click();
  const trace = answer.getByRole("region", { name: "Trace" });
  const tree = trace.getByRole("tree", { name: "Steps" });
  await expect(tree.locator('[data-span-kind="tool"]')).toHaveCount(3);
  await expect(tree.locator('[data-span-kind="tool"]').first()).toContainText("tool.search_code");
  await expect(tree.locator('[data-span-kind="tool"]').first()).toHaveAttribute("aria-level", "3");
  await expect(tree.locator('[data-span-kind="llm"]')).toHaveCount(4);
  await expect(tree.locator('[data-span-kind="llm"]').first()).toContainText("claude-haiku-5-5");
  await expect(trace.getByTestId("trace-totals")).toContainText("Cost$0.0133");

  expect(dialogs).toEqual([]);
});

test("the steps stay inside the panel and nothing covers the mode toggle", async ({ page }) => {
  await page.goto("/s/1");
  await waitForMap(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  const modes = panel.getByRole("radiogroup", { name: "Answer mode" });
  await modes.getByText("Agent", { exact: true }).click();
  await panel
    .getByRole("textbox", { name: "Question" })
    .fill("How does create_app work, in as much detail as the code allows for this question?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();
  const steps = panel.getByRole("region", { name: "Agent steps" });
  await expect(steps.getByRole("listitem")).toHaveCount(5);

  // A long argument summary is cut with an ellipsis instead of widening the panel.
  const bounds = await panel.boundingBox();
  const box = await steps.boundingBox();
  expect(bounds && box).toBeTruthy();
  expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
  expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width);
  const overflow = await panel.evaluate((element) => {
    const scroller = element.querySelector(".overflow-y-auto")!;
    return scroller.scrollWidth - scroller.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(0);

  // The toggle can be used again: nothing sits on top of it.
  await modes.getByText("Quick", { exact: true }).click({ trial: true });
  await modes.getByText("Quick", { exact: true }).click();
  await expect(modes.getByRole("radio", { name: "Quick" })).toBeChecked();
});

test("a question the agent finds nothing for is refused, with its one search shown", async ({
  page,
}) => {
  await page.goto("/s/1");
  await waitForMap(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel
    .getByRole("radiogroup", { name: "Answer mode" })
    .getByText("Agent", { exact: true })
    .click();
  await panel.getByRole("textbox", { name: "Question" }).fill("How are refunds sent to the bank?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("Not enough evidence");
  const rows = panel.getByRole("region", { name: "Agent steps" }).getByRole("listitem");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("0 pieces of evidence");
  await expect(rows.nth(1)).toContainText("Finished exploring");
});
