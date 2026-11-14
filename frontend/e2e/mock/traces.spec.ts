import { expect, test } from "@playwright/test";
import { failOnDialogs, waitForMap } from "../helpers";

test("an answer's cost badge opens its trace: steps, model, tokens, cost, time", async ({
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
  const badge = answer.getByTestId("cost-badge");
  await expect(badge).toContainText("$0.0062");
  await expect(badge).toContainText("tokens");
  await expect(answer.getByRole("region", { name: "Trace" })).toHaveCount(0);
  await badge.click();

  const trace = answer.getByRole("region", { name: "Trace" });
  await expect(trace.getByTestId("trace-totals")).toContainText("Cost$0.0062");
  await expect(trace.getByTestId("trace-totals")).toContainText("Time1.84 s");
  const steps = trace.getByRole("tree", { name: "Steps" });
  await expect(steps.getByRole("treeitem")).toHaveCount(4);
  const llm = steps.locator('[data-span-kind="llm"]');
  await expect(llm).toContainText("llm.call");
  await expect(llm).toContainText("claude-sonnet-5-5");
  await expect(llm).toContainText("$0.0062");
  await expect(steps.locator('[data-span-kind="retrieval"]')).toContainText("retrieve");
  await expect(steps.locator('[data-span-kind="validation"]')).toContainText("validate");

  // Steps collapse under their parent.
  await steps.getByRole("button", { name: "Collapse answer" }).click();
  await expect(steps.getByRole("treeitem")).toHaveCount(1);
  await steps.getByRole("button", { name: "Expand answer" }).click();
  await expect(steps.getByRole("treeitem")).toHaveCount(4);

  // The badge closes it again.
  await badge.click();
  await expect(answer.getByRole("region", { name: "Trace" })).toHaveCount(0);
  expect(dialogs).toEqual([]);
});

test("a refused answer still has a trace, without a model step", async ({ page }) => {
  await page.goto("/s/1");
  await waitForMap(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel.getByRole("textbox", { name: "Question" }).fill("How are refunds sent to the bank?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = panel.getByRole("region", { name: "Answer" });
  await answer.getByTestId("cost-badge").click();
  const steps = answer.getByRole("tree", { name: "Steps" });
  await expect(steps.getByRole("treeitem")).toHaveCount(2);
  await expect(steps.locator('[data-span-kind="llm"]')).toHaveCount(0);
  await expect(answer.getByTestId("trace-totals")).toContainText("Cost$0.0000");
});

test("an impact report and its explanation each show their own trace", async ({ page }) => {
  await page.goto("/s/1");
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

  const analysis = panel.getByRole("button", { name: "Trace of the analysis" });
  await analysis.click();
  const analysisTrace = panel.getByRole("region", { name: "Trace" });
  await expect(analysisTrace.getByRole("treeitem")).toHaveCount(1);
  await expect(analysisTrace).toContainText("impact.analyze");
  await analysis.click();
  await expect(panel.getByRole("region", { name: "Trace" })).toHaveCount(0);

  await panel.getByRole("button", { name: "Explain", exact: true }).click();
  const explanation = panel.getByRole("region", { name: "Explanation" });
  await explanation.getByTestId("cost-badge").click();
  const steps = explanation.getByRole("tree", { name: "Steps" });
  await expect(steps.getByRole("treeitem")).toHaveCount(4);
  await expect(steps).toContainText("impact.explain");
  await expect(steps).toContainText("impact.evidence");
  await expect(explanation.getByTestId("trace-totals")).toContainText("Cost$0.0048");
});
