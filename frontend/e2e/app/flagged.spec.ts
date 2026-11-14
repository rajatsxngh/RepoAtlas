import { expect, test } from "@playwright/test";
import { failOnDialogs, waitForMap } from "../helpers";

// The planted-injection fixture through the real API: the detector flags the cited code, the
// answer's citations carry the flags, and the panel shows the warning. The model is the offline
// stand-in, so this checks the plumbing and the UI, not how a model reacts to the planted text.

test("citations of code with planted instructions carry a warning", async ({ page }) => {
  test.setTimeout(120_000);
  const dialogs = failOnDialogs(page);
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/repoatlas-e2e/lab");
  await page.getByRole("button", { name: /Map it/ }).click();
  await page.waitForURL(/\/s\/\d+$/, { timeout: 60_000 });
  await waitForMap(page);

  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Ask" });
  await panel
    .getByRole("textbox", { name: "Question" })
    .fill("How does charge compute the total it sends to the gateway?");
  await panel.getByRole("button", { name: "Ask", exact: true }).click();

  const answer = panel.getByRole("region", { name: "Answer" });
  await expect(answer).toContainText("no model wrote this answer", { timeout: 30_000 });
  // The stand-in repeats nothing from the repository: no canary reaches the page.
  await expect(answer).not.toContainText("CANARY");
  await expect(answer.getByTestId("answer-metrics")).toContainText(
    "code excerpts flagged as instruction-like",
  );

  const flagged = answer.locator('[data-flagged="true"]');
  await expect(flagged.first()).toBeVisible();
  const icon = flagged.first().getByRole("img");
  await expect(icon).toHaveAccessibleName(/written to steer an AI/);
  await expect(icon).toHaveAccessibleName(/treated as data, not followed/);

  // The flagged code opens like any other citation, as inert text.
  await flagged.first().click();
  await expect(flagged.first()).toHaveAttribute("aria-pressed", "true");
  await expect(panel.getByRole("figure")).toBeVisible();
  // The planted text is on the page as text; none of it became markup or ran.
  await expect(panel.getByRole("figure")).toContainText("def ");
  expect(dialogs).toEqual([]);
});
