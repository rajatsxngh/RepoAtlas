import { expect, test } from "@playwright/test";

test("builds a repository and opens its map", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/RepoAtlas/);
  await expect(
    page.getByRole("heading", { level: 1, name: "See how a repository fits together" }),
  ).toBeVisible();
  const input = page.getByLabel("GitHub repository URL");
  await input.fill("repoatlas-fixtures/ts-small");
  await input.blur();
  await page
    .getByRole("button", { name: "Use https://github.com/repoatlas-fixtures/ts-small" })
    .click();
  const progress = page.getByRole("region", { name: "Building the map" });
  await expect(progress).toBeVisible();
  await expect(progress.getByRole("progressbar", { name: "Build progress" })).toBeVisible();
  await expect(progress.getByRole("list", { name: "Build stages" })).toContainText(
    "Parsing the code",
  );
  await page.waitForURL("**/s/2");
  await expect(page.getByText("repoatlas-fixtures/ts-small")).toBeVisible();
});

test("explains why a private repository can't be mapped", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/acme/private");
  await page.getByRole("button", { name: /Map it/ }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "This repository is private" }),
  ).toBeVisible();
});
