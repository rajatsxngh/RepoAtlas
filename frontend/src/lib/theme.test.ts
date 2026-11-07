import { describe, expect, it } from "vitest";
import {
  THEME_BOOT_SCRIPT,
  THEME_STORAGE_KEY,
  applyThemeChoice,
  nextThemeChoice,
  readThemeChoice,
  resolvedTheme,
} from "./theme";

function memoryStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
    items,
  };
}

describe("theme", () => {
  it("reads only light or dark from storage", () => {
    expect(readThemeChoice(memoryStorage({ [THEME_STORAGE_KEY]: "dark" }))).toBe("dark");
    expect(readThemeChoice(memoryStorage({ [THEME_STORAGE_KEY]: "<b>x</b>" }))).toBe("system");
    expect(readThemeChoice(undefined)).toBe("system");
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readThemeChoice(broken)).toBe("system");
  });

  it("pins and unpins the theme on the root element", () => {
    const root = document.createElement("html");
    const storage = memoryStorage();
    applyThemeChoice("dark", root, storage);
    expect(root.dataset.theme).toBe("dark");
    expect(storage.items.get(THEME_STORAGE_KEY)).toBe("dark");
    applyThemeChoice("system", root, storage);
    expect(root.dataset.theme).toBeUndefined();
    expect(storage.items.has(THEME_STORAGE_KEY)).toBe(false);
  });

  it("resolves and cycles choices", () => {
    expect(resolvedTheme("system", true)).toBe("dark");
    expect(resolvedTheme("system", false)).toBe("light");
    expect(resolvedTheme("light", true)).toBe("light");
    expect(["system", "light", "dark"].map((c) => nextThemeChoice(c as never))).toEqual([
      "light",
      "dark",
      "system",
    ]);
  });

  it("boot script applies a stored choice and survives blocked storage", () => {
    const run = new Function("localStorage", "document", THEME_BOOT_SCRIPT);
    const root = document.createElement("html");
    run(memoryStorage({ [THEME_STORAGE_KEY]: "light" }), { documentElement: root });
    expect(root.dataset.theme).toBe("light");
    const other = document.createElement("html");
    expect(() =>
      run(
        {
          getItem: () => {
            throw new Error("blocked");
          },
        },
        { documentElement: other },
      ),
    ).not.toThrow();
    expect(other.dataset.theme).toBeUndefined();
  });
});
