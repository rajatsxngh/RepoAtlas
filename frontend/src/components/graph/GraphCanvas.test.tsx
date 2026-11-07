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
