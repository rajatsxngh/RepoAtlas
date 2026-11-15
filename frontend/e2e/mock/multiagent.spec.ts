import { expect, test } from "@playwright/test";
import { failOnDialogs, mapNode, waitForMap } from "../helpers";

const CREATE_APP = "py:src/shop/app.py:create_app:function";

test("multi-agent mode shows its branches, a verified answer, and a trace grouped by branch", async ({
  page,
}) => {
  const dialogs = failOnDialogs(page);
  await page.goto("/s/1");
  await waitForMap(page);

  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  const modes = panel.getByRole("radiogroup", { name: "Answer mode" });
  await expect(modes.getByRole("radio")).toHaveCount(3);
  // The radio itself is visually hidden; its label is what a person clicks.
  await modes.getByText("Multi-agent", { exact: true }).click();
  await expect(modes.getByRole("radio", { name: "Multi-agent" })).toBeChecked();
  await expect(panel.getByTestId("ask-mode-hint")).toContainText("checks every claim");

  await panel.getByRole("textbox", { name: "Question" }).fill("How does create_app work?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();

  // The steps: the plan, then each branch's own steps under its sub-question, then the
  // writing and the verification.
  const steps = panel.getByRole("region", { name: "Agent steps" });
  await expect(steps.getByTestId("steps-summary")).toContainText("2 branches · 3 tool calls");
  await expect(steps.getByRole("list", { name: "Before the branches" })).toContainText(
    "Planned the exploration",
  );
  const first = steps.getByRole("region", { name: "Branch b1" });
  await expect(first).toContainText("Where is `create_app` defined, and what does it do?");
  const firstRows = first.getByRole("listitem");
  await expect(firstRows).toHaveCount(4);
  await expect(firstRows.nth(0)).toContainText("find_definition");
  await expect(firstRows.nth(1)).toContainText("read_file");
  await expect(firstRows.nth(2)).toContainText("Finished exploring");
  await expect(firstRows.nth(3)).toContainText("Reported its findings");
  await expect(first.getByTestId("branch-state")).toHaveText("explored");
  const second = steps.getByRole("region", { name: "Branch b2" });
  await expect(second).toContainText("What calls `create_app`?");
  await expect(second.getByRole("listitem")).toHaveCount(3);
  await expect(second.getByRole("listitem").first()).toContainText("find_callers");
  const after = steps.getByRole("list", { name: "After the branches" }).getByRole("listitem");
  await expect(after).toHaveCount(2);
  await expect(after.nth(0)).toContainText("Wrote the answer");
  await expect(after.nth(1)).toContainText("Checked the claims against the code");
  await expect(steps).toHaveAttribute("aria-busy", "false");

  // The answer: only the verified claim, and what the verifier left out.
  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("Verified statements about the code:");
  await expect(
    answer.getByRole("list", { name: "Claims", exact: true }).getByRole("listitem"),
  ).toHaveCount(1);
  const verification = answer.getByRole("group", { name: "Verification" });
  await expect(verification).toContainText("1 of 2 claims verified against the code they cite");
  await expect(verification.getByRole("list", { name: "Claims left out" })).toContainText(
    "not supported: The cited lines define the symbol",
  );
  const badge = answer.getByTestId("cost-badge");
  await expect(badge).toContainText("$0.0238");
  const chip = answer.getByRole("list", { name: "Claims", exact: true }).getByRole("button", {
    name: "app.py:22–23",
  });
  await chip.click();
  await expect(panel.getByRole("figure")).toContainText("def create_app");
  await expect(mapNode(page, CREATE_APP).locator('[data-highlight="primary"]')).toHaveCount(1);

  // The trace: each branch is one labeled group with its turns and tool calls inside.
  await badge.click();
  const trace = answer.getByRole("region", { name: "Trace" });
  const tree = trace.getByRole("tree", { name: "Steps" });
  const branches = tree.locator("[data-branch]");
  await expect(branches).toHaveCount(2);
  await expect(branches.nth(0)).toHaveAttribute("data-branch", "b1");
  await expect(branches.nth(0)).toContainText("branch b1");
  await expect(branches.nth(0).getByTestId("branch-status")).toHaveText("complete");
  await expect(branches.nth(0).locator('[data-span-kind="tool"]')).toHaveCount(2);
  await expect(branches.nth(1).locator('[data-span-kind="tool"]')).toHaveCount(1);
  await expect(branches.nth(1).locator('[data-span-kind="tool"]')).toContainText(
    "tool.find_callers",
  );
  await expect(tree.locator('[data-span-kind="tool"]').first()).toHaveAttribute("aria-level", "4");
  await expect(trace.getByTestId("trace-totals")).toContainText("Cost$0.0238");
  // Folding a branch hides its steps and nothing else.
  await tree.getByRole("button", { name: "Collapse branch b1" }).click();
  await expect(tree.locator('[data-span-kind="tool"]')).toHaveCount(1);

  expect(dialogs).toEqual([]);
});

test("the branches stay inside the panel and nothing covers the mode toggle", async ({ page }) => {
  await page.goto("/s/1");
  await waitForMap(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  const modes = panel.getByRole("radiogroup", { name: "Answer mode" });
  await modes.getByText("Multi-agent", { exact: true }).click();
  await panel.getByRole("textbox", { name: "Question" }).fill("How does create_app work?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();
  const steps = panel.getByRole("region", { name: "Agent steps" });
  await expect(steps.getByRole("region", { name: "Branch b2" })).toBeVisible();

  // Nothing is wider than the panel: sub-questions wrap and long arguments are cut.
  const bounds = await panel.boundingBox();
  for (const name of ["Branch b1", "Branch b2"]) {
    const box = await steps.getByRole("region", { name }).boundingBox();
    expect(bounds && box).toBeTruthy();
    expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
    expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width);
  }
  const overflow = await panel.evaluate((element) => {
    const scroller = element.querySelector(".overflow-y-auto")!;
    return scroller.scrollWidth - scroller.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(0);

  // All three mode labels are on one row and none overlaps another.
  const labels = await Promise.all(
    ["Quick", "Agent", "Multi-agent"].map((label) =>
      modes.getByText(label, { exact: true }).boundingBox(),
    ),
  );
  for (let index = 1; index < labels.length; index += 1) {
    expect(labels[index]!.x).toBeGreaterThanOrEqual(
      labels[index - 1]!.x + labels[index - 1]!.width,
    );
    expect(labels[index]!.y).toBe(labels[0]!.y);
  }
  // The toggle can be used again: nothing sits on top of it.
  await modes.getByText("Quick", { exact: true }).click({ trial: true });
  await modes.getByText("Agent", { exact: true }).click();
  await expect(modes.getByRole("radio", { name: "Agent", exact: true })).toBeChecked();
});

test("a question nothing is found for is refused, with its one branch shown", async ({ page }) => {
  await page.goto("/s/1");
  await waitForMap(page);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel
    .getByRole("radiogroup", { name: "Answer mode" })
    .getByText("Multi-agent", { exact: true })
    .click();
  await panel.getByRole("textbox", { name: "Question" }).fill("How are refunds sent to the bank?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("Not enough evidence");
  await expect(answer.getByRole("group", { name: "Verification" })).toHaveCount(0);
  const branch = panel.getByRole("region", { name: "Branch b1" });
  await expect(branch).toContainText("How are refunds sent to the bank?");
  await expect(branch.getByRole("listitem")).toHaveCount(2);
  await expect(branch.getByRole("listitem").first()).toContainText("0 pieces of evidence");
});
