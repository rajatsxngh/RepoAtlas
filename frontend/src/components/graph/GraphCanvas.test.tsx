import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithApi } from "@/test/render";
import { GraphCanvas, type GraphCanvasHandle } from "./GraphCanvas";

function renderCanvas(snapshotId = 1, onSelect = vi.fn(), onNodeAction = vi.fn()) {
  renderWithApi(
    <div style={{ width: 1200, height: 800 }}>
      <GraphCanvas
        snapshotId={snapshotId}
        layers={[]}
        onSelect={onSelect}
        onNodeAction={onNodeAction}
        nodeActions={[
          { id: "focus", label: "Center on the map", appliesTo: ["dir", "file"] },
          { id: "never", label: "Only for tests", appliesTo: ["test"] },
        ]}
      />
    </div>,
  );
  return { onSelect, onNodeAction };
}

describe("GraphCanvas", () => {
  it("draws the top-level folders and the aggregated edges between them", async () => {
    renderCanvas();
    for (const name of ["scripts", "src", "stubs", "tests", "vendor"]) {
      expect(await screen.findByText(name)).toBeInTheDocument();
    }
    expect(screen.getByText("5 files")).toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector('[data-id="dir:tests|dir:src|calls"]')).not.toBeNull(),
    );
  });

  it("hides test links until the legend turns them on, and back off", async () => {
    renderCanvas();
    await waitFor(() =>
      expect(document.querySelector('[data-id="dir:tests|dir:src|calls"]')).not.toBeNull(),
    );
    const testLinks = '[data-id="dir:src|dir:tests|tested_by"]';
    expect(document.querySelector(testLinks)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Legend/ }));
    const toggle = screen.getByRole("button", { name: /Tested by/ });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(document.querySelector(testLinks)).not.toBeNull());
    // Other kinds turn off the same way.
    fireEvent.click(screen.getByRole("button", { name: /^Calls/ }));
    await waitFor(() =>
      expect(document.querySelector('[data-id="dir:tests|dir:src|calls"]')).toBeNull(),
    );
    expect(document.querySelector(testLinks)).not.toBeNull();
  });

  it("keeps the hovered node's edges strong and fades the rest, by node position only", async () => {
    renderCanvas();
    await waitFor(() =>
      expect(document.querySelector('[data-id="dir:tests|dir:src|calls"]')).not.toBeNull(),
    );
    const focusRules = () =>
      [...document.querySelectorAll("style")]
        .map((style) => style.textContent ?? "")
        .filter((text) => text.includes(".ra-edge[data-s="));
    expect(focusRules()).toHaveLength(0);
    const node = document.querySelector('.react-flow__node[data-id="dir:tests"]');
    expect(node).not.toBeNull();
    fireEvent.mouseEnter(node!);
    await waitFor(() => expect(focusRules()).toHaveLength(1));
    // The rule names a number, never the node's key or name.
    expect(focusRules()[0]).toMatch(/\[data-s="\d+"\]/);
    expect(focusRules()[0]).not.toContain("tests");
    const edge = document.querySelector('[data-id="dir:tests|dir:src|calls"] path.ra-edge');
    const index = edge?.getAttribute("data-s");
    expect(index).toMatch(/^\d+$/);
    expect(focusRules()[0]).toContain(`[data-s="${index}"]`);
    fireEvent.mouseLeave(node!);
    await waitFor(() => expect(focusRules()).toHaveLength(0));
  });

  it("selects a node on click", async () => {
    const { onSelect } = renderCanvas();
    // d3-zoom needs event.view on mousedown, which jsdom's synthetic pointer events lack.
    fireEvent.click(await screen.findByText("tests"));
    expect(onSelect).toHaveBeenCalledWith("dir:tests");
  });

  it("explains a snapshot that can't be read", async () => {
    renderCanvas(404);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found");
  });
});

describe("GraphCanvas expansion", () => {
  it("opens a folder into its children and closes it again", async () => {
    renderCanvas();
    fireEvent.click(await screen.findByRole("button", { name: "Open src" }));
    expect(await screen.findByText("shop")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Close src" })).toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector('[data-id="dir:tests|dir:src/shop|calls"]')).not.toBeNull(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close src" }));
    await waitFor(() => expect(screen.queryByText("shop")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Open src" })).toBeInTheDocument();
  });

  it("opens files into their symbols", async () => {
    renderCanvas();
    fireEvent.click(await screen.findByRole("button", { name: "Open src" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open shop" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open cart.py" }));
    expect(await screen.findByText("GiftCart")).toBeInTheDocument();
    expect(screen.getByText("Cart")).toBeInTheDocument();
  });
});

describe("GraphCanvas styling", () => {
  it("hides guesses until asked, then dashes them, and labels names as text", async () => {
    renderCanvas(3);
    expect(await screen.findByText("<img src=x onerror=alert('dir')>")).toBeInTheDocument();
    // This fixture's only edges are guesses: hidden by default.
    expect(document.querySelector('path[data-guess="true"]')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Legend" }));
    fireEvent.click(screen.getByRole("button", { name: /Guessed by name/ }));
    await waitFor(() => expect(document.querySelector('path[data-guess="true"]')).not.toBeNull());
    const guess = document.querySelector('path[data-guess="true"]') as SVGPathElement;
    expect(guess.style.strokeDasharray).not.toBe("");
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows the legend and the map controls", async () => {
    renderCanvas();
    expect(await screen.findByRole("region", { name: "Legend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close everything" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tidy the layout" })).toBeInTheDocument();
  });
});

describe("GraphCanvas context menu", () => {
  it("offers details, opening, and the actions registered for the node's kind", async () => {
    const { onNodeAction, onSelect } = renderCanvas();
    fireEvent.contextMenu(await screen.findByText("tests"));
    const menu = await screen.findByRole("menu", { name: "Actions for tests" });
    const items = within(menu)
      .getAllByRole("menuitem")
      .map((item) => item.textContent);
    expect(items).toEqual(["Show details", "Open", "Center on the map"]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Center on the map" }));
    expect(onNodeAction).toHaveBeenCalledWith("dir:tests", "focus");
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.contextMenu(screen.getByText("src"));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Show details" }));
    expect(onSelect).toHaveBeenCalledWith("dir:src");
  });

  it("moves with the arrow keys and closes on Escape", async () => {
    renderCanvas();
    fireEvent.contextMenu(await screen.findByText("tests"));
    const menu = await screen.findByRole("menu");
    const [first, second] = within(menu).getAllByRole("menuitem");
    expect(first).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(second).toHaveFocus();
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("GraphCanvas reveal", () => {
  async function renderWithHandle(nodeCap?: number) {
    const handle: { current: GraphCanvasHandle | null } = { current: null };
    renderWithApi(
      <div style={{ width: 1200, height: 800 }}>
        <GraphCanvas
          ref={handle}
          snapshotId={1}
          layers={[]}
          onSelect={vi.fn()}
          onNodeAction={vi.fn()}
          nodeCap={nodeCap}
        />
      </div>,
    );
    await screen.findByText("src");
    return handle;
  }

  it("opens every ancestor of a nested symbol, root first", async () => {
    const handle = await renderWithHandle();
    const key = "py:src/shop/utils/helpers.py:format_price.with_symbol:function";
    await expect(handle.current!.revealNode(key)).resolves.toBe(true);
    expect(await screen.findByText("with_symbol")).toBeInTheDocument();
    for (const container of ["src", "shop", "utils", "helpers.py", "format_price"]) {
      expect(screen.getByRole("button", { name: `Close ${container}` })).toBeInTheDocument();
    }
    expect(document.querySelector(`[data-id="${key}"]`)).not.toBeNull();
  });

  it("brings a node out of a +N more group", async () => {
    const handle = await renderWithHandle(8);
    await expect(handle.current!.revealNode("dir:vendor")).resolves.toBe(true);
    await expect(handle.current!.revealNode("file:src/shop/utils/helpers.py")).resolves.toBe(true);
    expect(await screen.findByText("helpers.py")).toBeInTheDocument();
    const state = screen.getByTestId("map-state");
    expect(Number(state.dataset.visibleNodes)).toBeLessThanOrEqual(8);
  });

  it("says so when a node doesn't exist", async () => {
    const handle = await renderWithHandle();
    await expect(handle.current!.revealNode("py:nope.py:ghost:function")).resolves.toBe(false);
    expect(await screen.findByText(/Couldn't find py:nope.py:ghost:function/)).toBeInTheDocument();
  });
});

describe("GraphCanvas layers", () => {
  it("draws layers: bubbled nodes get a count badge, layer edges are recolored, others fade", async () => {
    const add = "py:src/shop/cart.py:Cart.add:method";
    const test = "py:tests/test_cart.py:test_add_rounds_prices:test";
    renderWithApi(
      <div style={{ width: 1200, height: 800 }}>
        <GraphCanvas
          snapshotId={1}
          layers={[
            {
              id: "path",
              nodeKeys: [test, add],
              edgeKeys: [`${test}|${add}|calls`],
              style: "primary",
              label: "Path",
            },
            {
              id: "risk",
              nodeKeys: ["dir:vendor"],
              style: "warning",
              scores: { "dir:vendor": 0.5 },
            },
          ]}
          onSelect={vi.fn()}
          onNodeAction={vi.fn()}
        />
      </div>,
    );
    await screen.findByText("src");
    await waitFor(() => expect(screen.getAllByLabelText("1 highlighted inside")).toHaveLength(2));
    expect(document.querySelector('[data-highlight="warning"]')).not.toBeNull();
    await waitFor(() =>
      expect(document.querySelector('path[data-highlight="primary"]')).not.toBeNull(),
    );
    const others = [
      ...document.querySelectorAll("path.ra-edge:not([data-highlight])"),
    ] as SVGPathElement[];
    expect(others.length).toBeGreaterThan(0);
    expect(others.every((edge) => edge.style.opacity === "0.22")).toBe(true);
  });
});
