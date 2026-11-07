import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { NodeAction } from "@/components/graph/layers";
import { XSS_PAYLOADS } from "@/lib/api/mock/fixtures/xss";
import { renderWithApi } from "@/test/render";
import { NodePanel } from "./NodePanel";

const ACTIONS: NodeAction[] = [
  { id: "trace", label: "Trace it", appliesTo: ["method", "function"] },
  { id: "dirs-only", label: "Folder thing", appliesTo: ["dir"] },
];

describe("NodePanel", () => {
  it("shows a method's kind, key, place, connections, owners, actions, and code", async () => {
    const onAction = vi.fn();
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderWithApi(
      <NodePanel
        snapshotId={1}
        nodeKey="py:src/shop/cart.py:Cart.add:method"
        actions={ACTIONS}
        onAction={onAction}
        onNavigate={onNavigate}
      />,
    );
    const panel = screen.getByRole("complementary", { name: "Node details" });
    expect(await within(panel).findByRole("heading", { name: "add" })).toBeVisible();
    expect(panel).toHaveTextContent("Method");
    expect(panel).toHaveTextContent("Cart.add");
    expect(panel).toHaveTextContent("src/shop/cart.py · lines 14–15");
    expect(panel).toHaveTextContent("py:src/shop/cart.py:Cart.add:method");

    expect(await within(panel).findByRole("button", { name: "add_item" })).toBeVisible();
    expect(within(panel).getByRole("button", { name: "round_money" })).toBeVisible();
    expect(panel).toHaveTextContent("Tested by 2 tests");
    expect(panel).toHaveTextContent(/\d+%/);

    expect(within(panel).queryByRole("button", { name: "Folder thing" })).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "Trace it" }));
    expect(onAction).toHaveBeenCalledWith("py:src/shop/cart.py:Cart.add:method", "trace");
    await user.click(within(panel).getByRole("button", { name: "round_money" }));
    expect(onNavigate).toHaveBeenCalledWith("py:src/shop/utils/helpers.py:round_money:function");

    expect(await within(panel).findByRole("figure")).toHaveTextContent("def add(self");
  });

  it("shows importers and imports for a file, and no code for a folder", async () => {
    const { unmount } = renderWithApi(<NodePanel snapshotId={1} nodeKey="file:src/shop/cart.py" />);
    expect(await screen.findByText("Imported by")).toBeVisible();
    expect(screen.getByText("Imports")).toBeVisible();
    unmount();
    renderWithApi(<NodePanel snapshotId={1} nodeKey="dir:src" actions={ACTIONS} />);
    expect(await screen.findByText(/Open the folder on the map/)).toBeVisible();
    expect(screen.queryByRole("figure")).toBeNull();
    expect(screen.getByRole("button", { name: "Folder thing" })).toBeVisible();
  });

  it("renders names and owners from the repo as text", async () => {
    renderWithApi(
      <NodePanel snapshotId={3} nodeKey={`file:${XSS_PAYLOADS.dir}/${XSS_PAYLOADS.file}`} />,
    );
    const panel = screen.getByRole("complementary", { name: "Node details" });
    expect(await within(panel).findByText("<script>alert('file')</script>.py")).toBeVisible();
    expect(await within(panel).findByText("<img src=x onerror=alert('owner')>")).toBeVisible();
    expect(panel.querySelector("img, script, b")).toBeNull();
  });

  it("closes", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithApi(<NodePanel snapshotId={1} nodeKey="dir:src" onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Close details" }));
    expect(onClose).toHaveBeenCalled();
  });
});
