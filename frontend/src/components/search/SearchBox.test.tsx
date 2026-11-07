import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithApi } from "@/test/render";
import { SearchBox, markMatch } from "./SearchBox";

function renderSearch(snapshotId = 1, onPick = vi.fn()) {
  renderWithApi(<SearchBox snapshotId={snapshotId} onPick={onPick} debounceMs={0} slashShortcut />);
  return { onPick, input: screen.getByRole("combobox", { name: "Search symbols" }) };
}

describe("SearchBox", () => {
  it("lists matches as you type and picks with the keyboard", async () => {
    const user = userEvent.setup();
    const { onPick, input } = renderSearch();
    await user.type(input, "cart");
    const list = await screen.findByRole("listbox", { name: "Matching symbols" });
    await waitFor(() => expect(list.querySelectorAll('[role="option"]').length).toBeGreaterThan(1));
    expect(input).toHaveAttribute("aria-expanded", "true");
    const options = screen.getAllByRole("option");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(input).toHaveAttribute("aria-activedescendant", options[0]!.id);
    await user.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowUp}{Enter}");
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0]![0].name.toLowerCase()).toBe("cart");
  });

  it("picks with the mouse and shows where each match lives", async () => {
    const user = userEvent.setup();
    const { onPick, input } = renderSearch();
    await user.type(input, "with_sym");
    const option = await screen.findByRole("option", { name: /with_symbol/ });
    expect(option).toHaveTextContent("format_price.with_symbol · src/shop/utils/helpers.py:9");
    await user.click(option);
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({
        key: "py:src/shop/utils/helpers.py:format_price.with_symbol:function",
      }),
    );
  });

  it("says when nothing matches, and closes on Escape", async () => {
    const user = userEvent.setup();
    const { input } = renderSearch();
    await user.type(input, "zzzqqq");
    expect(await screen.findByText("No symbols match “zzzqqq”.")).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.keyboard("{Escape}");
    expect(input).toHaveValue("");
  });

  it("focuses on / from anywhere but text fields", async () => {
    const user = userEvent.setup();
    const { input } = renderSearch();
    expect(input).not.toHaveFocus();
    await user.keyboard("/");
    expect(input).toHaveFocus();
  });

  it("shows repo names with HTML in them as text", async () => {
    const user = userEvent.setup();
    const { input } = renderSearch(3);
    await user.type(input, "render");
    const option = await screen.findByRole("option", { name: /render/ });
    expect(option.querySelector("img, script, iframe")).toBeNull();
  });
});

describe("markMatch", () => {
  it("marks the first match without making markup from the text", () => {
    const { container } = render(<p>{markMatch("<img src=x onerror=alert(1)> helper", "help")}</p>);
    expect(container.querySelector("mark")?.textContent).toBe("help");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("<img src=x onerror=alert(1)> helper");
  });
});
