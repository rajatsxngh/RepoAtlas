import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mockApi, renderWithApi } from "@/test/render";
import { MapWorkspace } from "./MapWorkspace";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

async function renderWorkspace() {
  const { api } = mockApi();
  const snapshot = await api.getSnapshot(1);
  renderWithApi(
    <div style={{ width: 1400, height: 900 }}>
      <MapWorkspace snapshot={snapshot} />
    </div>,
    { api },
  );
  await screen.findByText("src");
}

describe("MapWorkspace", () => {
  it("finds a symbol, reveals it on the map, and opens its details", async () => {
    const user = userEvent.setup();
    await renderWorkspace();
    await user.type(screen.getByRole("combobox", { name: "Search symbols" }), "round_mon");
    await user.click(await screen.findByRole("option", { name: /round_money/ }, { timeout: 5000 }));
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    expect(
      await within(panel).findByRole("heading", { name: "round_money" }, { timeout: 5000 }),
    ).toBeVisible();
    // Revealing opens two folders and a file, each laid out in turn: slow when the suite runs in
    // parallel.
    await waitFor(
      () =>
        expect(
          document.querySelector('[data-id="py:src/shop/utils/helpers.py:round_money:function"]'),
        ).not.toBeNull(),
      { timeout: 8000 },
    );
  });

  it("highlights a path found from node actions and reveals every node on it", async () => {
    await renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "Path" }));
    expect(screen.getByRole("region", { name: "Path finder" })).toBeVisible();
    // Pick the ends through the path finder's own search boxes.
    const user = userEvent.setup();
    await user.type(screen.getByRole("combobox", { name: "Path from" }), "test_add");
    await user.click(
      await screen.findByRole("option", { name: /test_add_rounds_prices/ }, { timeout: 5000 }),
    );
    await user.type(screen.getByRole("combobox", { name: "Path to" }), "round_money");
    await user.click(await screen.findByRole("option", { name: /round_money/ }, { timeout: 5000 }));
    const steps = await screen.findByRole("list", { name: "Path steps" });
    const count = within(steps).getAllByRole("button").length;
    expect(count).toBeGreaterThan(1);
    await waitFor(
      () =>
        expect(
          document.querySelectorAll('[data-highlight="primary"]').length,
        ).toBeGreaterThanOrEqual(count),
      { timeout: 5000 },
    );
    for (const key of [
      "py:tests/test_cart.py:test_add_rounds_prices:test",
      "py:src/shop/utils/helpers.py:round_money:function",
    ]) {
      await waitFor(() => expect(document.querySelector(`[data-id="${key}"]`)).not.toBeNull(), {
        timeout: 8000,
      });
    }
    // The legend lists the layer.
    fireEvent.click(screen.getByRole("button", { name: /Legend/ }));
    expect(screen.getByText("Path: test_add_rounds_prices → round_money")).toBeVisible();
  });
});
