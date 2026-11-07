/** Box sizes on the map. Shared by the canvas and the layout worker. */

export interface Size {
  width: number;
  height: number;
}

export const LEAF_HEIGHT = 44;
export const LEAF_MIN_WIDTH = 140;
export const LEAF_MAX_WIDTH = 260;
/** Padding, kind tile, gaps, and the open button around a leaf's label. */
const LEAF_CHROME = 88;
export const MORE_SIZE: Size = { width: 132, height: 40 };

/** Space around a container's children: header on top, padding on every side. */
export const CONTAINER_HEADER = 40;
export const CONTAINER_PAD_X = 16;
export const CONTAINER_PAD_TOP = 14;
export const CONTAINER_PAD_BOTTOM = 18;
export const CONTAINER_MIN_WIDTH = 200;

/** Rough width of a label in the node font (13px Geist), without measuring the DOM. */
export function textWidth(text: string): number {
  let width = 0;
  for (const char of text) {
    if (/[ilj.,:;'|!]/.test(char)) width += 3.6;
    else if (/[mwMW@%]/.test(char)) width += 10.4;
    else if (/[A-Z0-9_]/.test(char)) width += 8.2;
    else width += 7.1;
  }
  return Math.ceil(width);
}

export function leafSize(label: string): Size {
  const width = Math.min(LEAF_MAX_WIDTH, Math.max(LEAF_MIN_WIDTH, LEAF_CHROME + textWidth(label)));
  return { width, height: LEAF_HEIGHT };
}

/** A container's box around children laid out in `content`. */
export function containerSize(content: Size, label: string): Size {
  return {
    width: Math.max(
      CONTAINER_MIN_WIDTH,
      Math.min(LEAF_MAX_WIDTH + 80, 96 + textWidth(label)),
      Math.ceil(content.width) + 2 * CONTAINER_PAD_X,
    ),
    height: CONTAINER_HEADER + CONTAINER_PAD_TOP + Math.ceil(content.height) + CONTAINER_PAD_BOTTOM,
  };
}

/** Where a container's content starts, relative to its box. */
export const CONTENT_OFFSET = { x: CONTAINER_PAD_X, y: CONTAINER_HEADER + CONTAINER_PAD_TOP };
