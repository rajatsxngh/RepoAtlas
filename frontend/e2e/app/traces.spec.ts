import { expect, test, type Page } from "@playwright/test";
import { waitForMap } from "../helpers";

// Against the real API (its trace store, with the model replaced by the offline stand-in).
// Named to sort after map.spec.ts, which needs the first build of the shop repository.

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/shop");
  await page.getByRole("button", { name: /Map it/ }).click();
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  await waitForMap(page);
}

test("an answer from the API shows its complete trace", async ({ page }) => {
  await openShop(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel.getByRole("textbox", { name: "Question" }).fill("How does the cart add an item?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();

  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("no model wrote this answer", { timeout: 30_000 });
  const badge = answer.getByTestId("cost-badge");
  await expect(badge).toContainText("$0.0000");
  await badge.click();

  const trace = answer.getByRole("region", { name: "Trace" });
  await expect(trace).toContainText("answer · 4 steps");
  const steps = trace.getByRole("tree", { name: "Steps" });
  // The root step, then retrieval, the model call, and validation under it.
  await expect(steps.getByRole("treeitem")).toHaveCount(4);
  await expect(steps.locator('[data-span-kind="answer"]')).toHaveAttribute("aria-level", "1");
  for (const kind of ["retrieval", "llm", "validation"]) {
    await expect(steps.locator(`[data-span-kind="${kind}"]`)).toHaveAttribute("aria-level", "2");
  }
  await expect(steps.locator('[data-span-kind="llm"]')).toContainText("offline-stand-in");
  await expect(trace.getByTestId("trace-totals")).toContainText("Cost$0.0000");
});

test("an impact report and its explanation show their traces", async ({ page }) => {
  await openShop(page);
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
  await expect(panel.getByRole("region", { name: "Trace" })).toContainText("impact.analyze");
  await analysis.click();

  await panel.getByRole("button", { name: "Explain", exact: true }).click();
  const explanation = panel.getByRole("region", { name: "Explanation" });
  await expect(explanation).toContainText("no model wrote this answer", { timeout: 30_000 });
  await expect(explanation.getByRole("list", { name: "Claims" })).toBeVisible();
  await explanation.getByTestId("cost-badge").click();
  const trace = explanation.getByRole("region", { name: "Trace" });
  await expect(trace).toContainText("impact_explain · 4 steps");
  const steps = trace.getByRole("tree", { name: "Steps" });
  await expect(steps).toContainText("impact.explain");
  await expect(steps).toContainText("impact.evidence");
  await expect(steps).toContainText("llm.call");
  await expect(steps).toContainText("validate");
});
