import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

// React Flow measures the DOM; jsdom has no layout, so give it the boxes the app asked for.
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

Object.defineProperties(HTMLElement.prototype, {
  offsetHeight: {
    configurable: true,
    get(this: HTMLElement) {
      return Number.parseFloat(this.style.height) || 1;
    },
  },
  offsetWidth: {
    configurable: true,
    get(this: HTMLElement) {
      return Number.parseFloat(this.style.width) || 1;
    },
  },
});

(SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () =>
  ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
