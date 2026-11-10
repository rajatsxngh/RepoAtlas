import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MockGraph, type FixtureData } from "@/lib/api/mock/graph";
import pyData from "@/lib/api/mock/fixtures/data/py_small.json";
import { renderWithApi } from "@/test/render";
import { EdgePanel } from "./EdgePanel";

const graph = new MockGraph(pyData as unknown as FixtureData);
const heaviest = graph
  .subgraph("dir:.", "file")
  .edges.reduce((best, edge) => (edge.count > best.count ? edge : best));

describe("EdgePanel", () => {
  it("lists each link behind a line, with where it happens, and opens either end", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderWithApi(
      <EdgePanel
        snapshotId={1}
        edge={{ ...heaviest, src: heaviest.src, dst: heaviest.dst }}
        onNavigate={onNavigate}
      />,
    );
    const panel = screen.getByRole("complementary", { name: "Link details" });
    const list = await within(panel).findByRole("list", { name: "Links" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(heaviest.count);
    expect(panel).toHaveTextContent(`${heaviest.count}`);
    const [first] = within(list).getAllByRole("button");
    await user.click(first!);
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });
});
