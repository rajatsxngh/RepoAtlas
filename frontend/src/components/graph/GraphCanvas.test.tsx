import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithApi } from "@/test/render";
import { GraphCanvas } from "./GraphCanvas";

function renderCanvas(snapshotId = 1, onSelect = vi.fn()) {
  renderWithApi(
    <div style={{ width: 1200, height: 800 }}>
      <GraphCanvas snapshotId={snapshotId} layers={[]} onSelect={onSelect} onNodeAction={vi.fn()} />
    </div>,
  );
  return { onSelect };
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
    expect(document.querySelector('[data-id="dir:src|dir:tests|tested_by"]')).not.toBeNull();
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
  it("dashes edges that are only guesses and labels names as text", async () => {
    renderCanvas(3);
    expect(await screen.findByText("<img src=x onerror=alert('dir')>")).toBeInTheDocument();
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
