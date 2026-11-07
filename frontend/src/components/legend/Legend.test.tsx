import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Legend } from "./Legend";

describe("Legend", () => {
  it("opens to explain node kinds, edge kinds, confidence, and bundles", async () => {
    const user = userEvent.setup();
    render(<Legend />);
    const toggle = screen.getByRole("button", { name: /Legend/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    for (const text of ["Folder", "Test", "Calls", "Tested by", "Routes to", "Resolved"]) {
      expect(screen.getByText(text)).toBeVisible();
    }
    expect(screen.getByText("Guessed by name (below 0.6)")).toBeVisible();
  });

  it("lists active highlight layers with their size", async () => {
    const user = userEvent.setup();
    render(
      <Legend
        layers={[
          { id: "path", nodeKeys: ["a", "b", "c"], style: "primary", label: "Path: main → helper" },
          { id: "x", nodeKeys: ["d"], style: "warning" },
        ]}
      />,
    );
    expect(screen.getByText("2 layers")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Legend/ }));
    expect(screen.getByText("Path: main → helper")).toBeVisible();
    expect(screen.getByText("x")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
  });
});
