"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";
import { applyThemeChoice, nextThemeChoice, readThemeChoice, type ThemeChoice } from "@/lib/theme";
import { Button } from "./Button";

const LABELS: Record<ThemeChoice, string> = { system: "System", light: "Light", dark: "Dark" };

function storage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export function ThemeToggle() {
  const choice = useSyncExternalStore(
    subscribe,
    () => readThemeChoice(storage()),
    (): ThemeChoice => "system",
  );
  const next = nextThemeChoice(choice);
  const Icon = choice === "light" ? Sun : choice === "dark" ? Moon : Monitor;
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Theme: ${LABELS[choice]}. Switch to ${LABELS[next]}.`}
      title={`Theme: ${LABELS[choice]}`}
      onClick={() => {
        applyThemeChoice(next, document.documentElement, storage());
        listeners.forEach((listener) => listener());
      }}
      icon={<Icon className="h-4 w-4" aria-hidden="true" />}
    />
  );
}
