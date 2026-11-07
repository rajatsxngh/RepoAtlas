"use client";

import { createContext, useContext } from "react";

/** What nodes on the canvas can ask the canvas to do. */
export interface CanvasActions {
  select: (key: string) => void;
  toggle: (key: string) => void;
  showMore: (container: string) => void;
  openMenu: (key: string, at: { x: number; y: number }) => void;
}

const noop = () => {};

export const CanvasActionsContext = createContext<CanvasActions>({
  select: noop,
  toggle: noop,
  showMore: noop,
  openMenu: noop,
});

export function useCanvasActions(): CanvasActions {
  return useContext(CanvasActionsContext);
}
