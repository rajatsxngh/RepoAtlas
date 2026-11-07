import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// findBy* and waitFor give up after 1 s by default. With the whole suite running in parallel
// (and on CI runners) mock requests, debounces, and layouts can take longer; a real failure still
// fails, just later.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
});

// React Flow measures the DOM; jsdom has no layout, so give it the boxes the app asked for
// (and a screen-sized box otherwise, so the viewport isn't empty and nothing is culled).
// (Adapted from React Flow's testing guide.)
class ResizeObserverStub {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    this.callback([{ target } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

class DOMMatrixReadOnlyStub {
  m22: number;
  constructor(transform?: string) {
    const scale = transform?.match(/scale\(([1-9.]+)\)/)?.[1];
    this.m22 = scale !== undefined ? Number(scale) : 1;
  }
}

Object.assign(globalThis, {
  ResizeObserver: globalThis.ResizeObserver ?? ResizeObserverStub,
  DOMMatrixReadOnly: globalThis.DOMMatrixReadOnly ?? DOMMatrixReadOnlyStub,
});

/** An inline size in pixels, else the fallback (percentages and auto have no layout here). */
function pixels(value: string, fallback: number): number {
  return value.endsWith("px") ? Number.parseFloat(value) || fallback : fallback;
}

Object.defineProperties(HTMLElement.prototype, {
  offsetHeight: {
    configurable: true,
    get(this: HTMLElement) {
      return pixels(this.style.height, 800);
    },
  },
  offsetWidth: {
    configurable: true,
    get(this: HTMLElement) {
      return pixels(this.style.width, 1200);
    },
  },
});

(SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () =>
  ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
