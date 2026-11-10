import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithApi } from "@/test/render";
import { OverviewPanel, sizeLine } from "./OverviewPanel";

describe("OverviewPanel", () => {
  it("shows routes, core files, and test coverage, and opens items on the map", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderWithApi(<OverviewPanel snapshotId={1} onNavigate={onNavigate} />);
    const panel = screen.getByRole("complementary", { name: "Start here" });
    expect(await within(panel).findByText("Routes")).toBeVisible();
    expect(within(panel).getByText("Core files")).toBeVisible();
    expect(
      within(panel).getByRole("meter", { name: "Source files with linked tests" }),
    ).toBeVisible();
    const items = within(panel)
      .getAllByRole("button")
      .filter((b) => b.title);
    await user.click(items[0]!);
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it("renders payloads in names as text", async () => {
    renderWithApi(<OverviewPanel snapshotId={3} />);
    await screen.findByRole("heading", { name: "Start here" });
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("sizeLine", () => {
  it("counts the kinds a reader cares about, in a fixed order", () => {
    expect(sizeLine({ dir: 3, file: 1, function: 12, test: 2 })).toBe(
      "1 file · 12 functions · 2 tests",
    );
  });
});
