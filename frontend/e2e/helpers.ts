import { expect, type Page } from "@playwright/test";

export interface MapState {
  visibleNodes: number;
  renderedNodes: number;
  renderedEdges: number;
  layoutPending: number;
}

/** The canvas's own counters (a hidden element the map keeps current). */
export async function mapState(page: Page): Promise<MapState> {
  return page.getByTestId("map-state").evaluate((element) => {
    const data = (element as HTMLElement).dataset;
    return {
      visibleNodes: Number(data.visibleNodes),
      renderedNodes: Number(data.renderedNodes),
      renderedEdges: Number(data.renderedEdges),
      layoutPending: Number(data.layoutPending),
    };
  });
}

/** Waits until the map is drawn and nothing waits for layout. */
export async function waitForMap(page: Page): Promise<void> {
  await expect
    .poll(async () => {
      const state = await mapState(page);
      return state.renderedNodes > 0 && state.layoutPending === 0;
    })
    .toBe(true);
}

/** A node on the canvas by its symbol key. */
export function mapNode(page: Page, key: string) {
  return page.locator(`.react-flow__node[data-id="${key.replaceAll('"', '\\"')}"]`);
}

/**
 * Clicks a node's open button and waits until the node is drawn as a laid-out container.
 * Clicks through the DOM, so nodes outside the viewport work too. Returns the milliseconds.
 */
export async function openNode(page: Page, key: string): Promise<number> {
  const started = Date.now();
  await mapNode(page, key).evaluate((node) => {
    const button = node.querySelector<HTMLButtonElement>('button[aria-label^="Open "]');
    if (!button) throw new Error("This node has no open button.");
    button.click();
  });
  await expect
    .poll(
      async () =>
        (await mapNode(page, key).locator(".ra-container").count()) > 0 &&
        (await mapState(page)).layoutPending === 0,
      { timeout: 10_000, intervals: [25, 50, 100] },
    )
    .toBe(true);
  return Date.now() - started;
}

/** Fails the test if anything in the page opens a dialog (a payload's alert()). */
export function failOnDialogs(page: Page): string[] {
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });
  return dialogs;
}

/** Elements that only exist if repo text became markup. */
export async function injectedElements(page: Page): Promise<number> {
  return page.evaluate(
    () =>
      document.querySelectorAll(
        'img[src="x"], svg[onload], iframe, b[onmouseover], [onerror], [onclick], [onmouseover], textarea',
      ).length,
  );
}
