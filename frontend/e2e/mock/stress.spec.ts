import { expect, test, type Page } from "@playwright/test";
import { mapState, openNode, waitForMap } from "../helpers";

const CAP = 300;
const LIMIT_MS = 2000;

/**
 * Drawn nodes that can still be opened, biggest first: folders by their file count, then files
 * by their line count, then classes. Opening big folders fills the map toward the cap.
 */
async function openable(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const weight = (node: HTMLElement) => {
      const kind = node.querySelector<HTMLElement>(".ra-kind-tile")?.dataset.kind;
      const count = Number(
        (/([\d,]+) (files?|lines?)/.exec(node.textContent ?? "")?.[1] ?? "1").replaceAll(",", ""),
      );
      return (kind === "dir" ? 1e9 : kind === "file" ? 1e6 : 0) + count;
    };
    return [...document.querySelectorAll<HTMLElement>(".react-flow__node")]
      .filter((node) => node.querySelector('button[aria-label^="Open "]'))
      .sort((a, b) => weight(b) - weight(a))
      .map((node) => node.dataset.id!);
  });
}

test("the 10,000-node graph stays under the node cap and lays out every expansion in under 2 s", async ({
  page,
}) => {
  test.setTimeout(240_000);
  await page.goto("/s/4");
  await waitForMap(page);

  const timings: { key: string; ms: number; visible: number }[] = [];
  const opened = new Set<string>();
  for (let round = 0; round < 40; round += 1) {
    // Off-screen nodes aren't drawn, so fit everything into view before picking one.
    await page
      .locator('button[aria-label="Fit View"]')
      .evaluate((button: HTMLElement) => button.click());
    await page.waitForTimeout(400);
    const candidates = (await openable(page)).filter((key) => !opened.has(key));
    if (!candidates.length) break;
    const key = candidates[0]!;
    opened.add(key);
    const ms = await openNode(page, key);
    const state = await mapState(page);
    timings.push({ key, ms, visible: state.visibleNodes });
    expect(state.visibleNodes, `after opening ${key}`).toBeLessThanOrEqual(CAP);
  }

  // "+N more" nodes reveal more, still under the cap.
  for (let round = 0; round < 5; round += 1) {
    const more = page.locator('button[aria-label^="Show "]');
    if (!(await more.count())) break;
    await more.first().evaluate((button: HTMLElement) => button.click());
    await expect.poll(async () => (await mapState(page)).layoutPending).toBe(0);
    expect((await mapState(page)).visibleNodes).toBeLessThanOrEqual(CAP);
  }

  const slowest = Math.max(...timings.map((t) => t.ms));
  const sorted = timings.map((t) => t.ms).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  const peak = Math.max(...timings.map((t) => t.visible));
  test.info().annotations.push({
    type: "layout",
    description: `${timings.length} expansions; median ${median} ms, slowest ${slowest} ms; peak ${peak} visible nodes (cap ${CAP})`,
  });
  console.log(`stress: ${test.info().annotations.at(-1)!.description}`);
  expect(timings.length).toBeGreaterThanOrEqual(25);
  expect(peak).toBeGreaterThan(CAP * 0.8);
  expect(slowest).toBeLessThan(LIMIT_MS);
});
