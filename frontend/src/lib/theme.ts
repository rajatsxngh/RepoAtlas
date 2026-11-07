/**
 * Light and dark themes. By default the app follows the system; a choice made with the theme
 * toggle pins `<html data-theme>` and is remembered in localStorage.
 */

export type ThemeChoice = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "repoatlas-theme";

/** Runs before the page paints, so a pinned theme never flashes the other one. */
export const THEME_BOOT_SCRIPT = `try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export function readThemeChoice(storage: Pick<Storage, "getItem"> | undefined): ThemeChoice {
  try {
    const value = storage?.getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyThemeChoice(
  choice: ThemeChoice,
  root: HTMLElement,
  storage: Pick<Storage, "setItem" | "removeItem"> | undefined,
): void {
  if (choice === "system") {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = choice;
  }
  try {
    if (choice === "system") storage?.removeItem(THEME_STORAGE_KEY);
    else storage?.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // Storage can be unavailable (private windows, blocked site data); the choice still applies.
  }
}

/** The theme in effect: the pinned choice, else the system's. */
export function resolvedTheme(choice: ThemeChoice, prefersDark: boolean): "light" | "dark" {
  if (choice === "system") return prefersDark ? "dark" : "light";
  return choice;
}

export function nextThemeChoice(choice: ThemeChoice): ThemeChoice {
  return choice === "system" ? "light" : choice === "light" ? "dark" : "system";
}
