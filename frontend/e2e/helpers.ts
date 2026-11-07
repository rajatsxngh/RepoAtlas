import { expect, type Page } from "@playwright/test";

export interface MapState {
  visibleNodes: number;
  renderedNodes: number;
  renderedEdges: number;
  layoutPending: number;
  /** How long ELK took for the latest layout request, in ms (0 before the first). */
  layoutMs: number;
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
      layoutMs: Number(data.layoutMs || 0),
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
 * Clicks through the DOM, so nodes outside the viewport work too. Returns the milliseconds,
 * measured inside the page: polling from the test over the DevTools protocol added up to a
 * second per expansion on a busy machine.
 */
export async function openNode(page: Page, key: string): Promise<number> {
  return mapNode(page, key).evaluate(async (node, id) => {
    const button = node.querySelector<HTMLButtonElement>('button[aria-label^="Open "]');
    if (!button) throw new Error("This node has no open button.");
    const state = document.querySelector<HTMLElement>('[data-testid="map-state"]');
    // Compared by id, not by selector: ids hold repo text (the XSS fixture's payloads).
    const drawnAsContainer = () =>
      [...document.querySelectorAll<HTMLElement>(".react-flow__node")].some(
        (element) => element.dataset.id === id && element.querySelector(".ra-container"),
      );
    const started = performance.now();
    button.click();
    while (performance.now() - started < 10_000) {
      await new Promise((resolve) => setTimeout(resolve, 10));
      if (state?.dataset.layoutPending === "0" && drawnAsContainer()) {
        return Math.round(performance.now() - started);
      }
    }
    throw new Error("The node wasn't drawn as a laid-out container within 10 s.");
  }, key);
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
