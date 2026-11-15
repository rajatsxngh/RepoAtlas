import { expect, test, type Page } from "@playwright/test";
import { waitForMap } from "../helpers";

// Agent mode against the real API: its tools, ledger, validator, and trace store, with the
// model replaced by the offline stand-in (it searches once, then answers from what the search
// listed). Named to sort after map.spec.ts, which needs the first build of the shop repository.

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/shop");
  await page.getByRole("button", { name: /Map it/ }).click();
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  await waitForMap(page);
}

test("an agent answer from the API streams its steps and stores them in its trace", async ({
  page,
}) => {
  await openShop(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel.getByRole("radiogroup", { name: "Answer mode" }).getByText("Agent").click();
  await panel.getByRole("textbox", { name: "Question" }).fill("How does the cart add an item?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();

  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("no model wrote this answer", { timeout: 30_000 });

  // One search by the stand-in, the end of the exploration, and the final answer call.
  const steps = panel.getByRole("region", { name: "Agent steps" });
  const rows = steps.getByRole("listitem");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("search_code");
  await expect(rows.nth(0)).toContainText('query="How does the cart add an item?"');
  await expect(rows.nth(0)).toContainText("of evidence");
  await expect(rows.nth(0)).not.toContainText("0 pieces of evidence");
  await expect(rows.nth(1)).toContainText("Finished exploring");
  await expect(rows.nth(2)).toContainText("Wrote the answer");
  await expect(rows.nth(2)).toContainText("offline-stand-in");
  await expect(steps.getByTestId("steps-summary")).toContainText("1 tool call · $0.0000");

  // The answer cites code the search found; a citation opens it.
  const chip = answer.getByRole("list", { name: "Claims" }).getByRole("button").first();
  await chip.click();
  await expect(chip).toHaveAttribute("aria-pressed", "true");
  await expect(panel.getByRole("figure")).toBeVisible();

  // The stored trace: three model calls (two exploration turns, the final call), one tool.
  await answer.getByTestId("cost-badge").click();
  const trace = answer.getByRole("region", { name: "Trace" });
  const tree = trace.getByRole("tree", { name: "Steps" });
  await expect(tree.locator('[data-span-kind="tool"]')).toHaveCount(1);
  await expect(tree.locator('[data-span-kind="tool"]')).toContainText("tool.search_code");
  await expect(tree.locator('[data-span-kind="llm"]')).toHaveCount(3);
  await expect(tree.locator('[data-span-kind="agent"]')).toHaveCount(3);
  await expect(tree.locator('[data-span-kind="validation"]')).toHaveCount(1);
  await expect(trace.getByTestId("trace-totals")).toContainText("Cost$0.0000");
});
