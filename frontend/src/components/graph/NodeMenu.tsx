"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";

export interface MenuItem {
  id: string;
  label: string;
}

export interface NodeMenuProps {
  /** Shown as the menu's accessible name. */
  title: string;
  items: readonly MenuItem[];
  at: { x: number; y: number };
  onPick: (id: string) => void;
  onClose: () => void;
}

/** A small context menu: arrow keys move, Enter picks, Escape or a click elsewhere closes. */
export function NodeMenu({ title, items, at, onPick, onClose }: NodeMenuProps) {
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    menu.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const away = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node)) onClose();
    };
    window.addEventListener("pointerdown", away, true);
    return () => window.removeEventListener("pointerdown", away, true);
  }, [onClose]);

  const onKeyDown = (event: KeyboardEvent) => {
    const buttons = [...(menu.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      buttons[(index + step + buttons.length) % buttons.length]?.focus();
    } else if (event.key === "Tab") {
      onClose();
    }
  };

  const left = Math.min(at.x, (typeof window === "undefined" ? 1200 : window.innerWidth) - 220);
  const top = Math.min(
    at.y,
    (typeof window === "undefined" ? 800 : window.innerHeight) - 40 * items.length - 16,
  );
  return (
    <div
      ref={menu}
      role="menu"
      aria-label={title}
      className="fixed z-50 min-w-48 rounded-xl border border-line bg-surface p-1 text-[13px] shadow-pop"
      style={{ left, top }}
      onKeyDown={onKeyDown}
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="menuitem"
          className="flex w-full cursor-pointer items-center rounded-lg px-2.5 py-1.5 text-left text-fg hover:bg-surface-2 focus:bg-surface-2 focus:outline-none"
          onClick={() => {
            onPick(item.id);
            onClose();
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
