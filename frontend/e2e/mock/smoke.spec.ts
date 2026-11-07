import { expect, test } from "@playwright/test";

test("the landing page renders", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/RepoAtlas/);
  await expect(page.getByRole("heading", { level: 1, name: "RepoAtlas" })).toBeVisible();
});
