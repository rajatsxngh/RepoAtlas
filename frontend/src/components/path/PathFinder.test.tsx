import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ApiNode, GraphPath } from "@/lib/api/types";
import { renderWithApi } from "@/test/render";
import { PathFinder, pathLayer } from "./PathFinder";

function Harness({
  onFound = vi.fn(),
  snapshotId = 1,
}: {
  onFound?: (path: GraphPath) => void;
  snapshotId?: number;
}) {
  const [from, setFrom] = useState<ApiNode | null>(null);
  const [to, setTo] = useState<ApiNode | null>(null);
  const [path, setPath] = useState<GraphPath | null>(null);
  return (
    <PathFinder
      snapshotId={snapshotId}
      from={from}
      to={to}
      onChangeFrom={setFrom}
      onChangeTo={setTo}
      path={path}
      onFound={(found) => {
        setPath(found);
        onFound(found);
      }}
      onClear={() => setPath(null)}
    />
  );
}

async function choose(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  text: string,
  option: RegExp,
) {
  await user.type(screen.getByRole("combobox", { name: label }), text);
  await user.click(await screen.findByRole("option", { name: option }));
}

describe("PathFinder", () => {
  it("finds the path between two picked symbols and lists its steps", async () => {
    const onFound = vi.fn();
    const user = userEvent.setup();
    renderWithApi(<Harness onFound={onFound} />);
    await choose(user, "Path from", "test_add", /test_add_rounds_prices/);
    await choose(user, "Path to", "round_money", /round_money/);
    const steps = await screen.findByRole("list", { name: "Path steps" });
    const names = within(steps)
      .getAllByRole("button")
      .map((button) => button.textContent);
    expect(names[0]).toContain("test_add_rounds_prices");
    expect(names.at(-1)).toContain("round_money");
    expect(steps).toHaveTextContent("calls");
    expect(onFound).toHaveBeenCalledTimes(1);
    const path = onFound.mock.calls[0]![0] as GraphPath;
    expect(path.nodes.length).toBe(names.length);
  });

  it("says when no path connects the two symbols", async () => {
    const user = userEvent.setup();
    renderWithApi(<Harness />);
    await choose(user, "Path from", "add_item", /add_item/);
    await choose(user, "Path to", "create_app", /create_app/);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No path connects add_item and create_app through calls or imports.",
    );
  });

  it("turns a path into a primary highlight layer", () => {
    const node = (key: string, name: string) => ({ key, name }) as ApiNode;
    const layer = pathLayer({
      nodes: [node("py:a.py:x:function", "x"), node("py:b.py:y:function", "y")],
      edges: [
        {
          src: "py:a.py:x:function",
          dst: "py:b.py:y:function",
          kind: "calls",
          provenance: "name_match",
          confidence: 0.9,
          meta: {},
        },
      ],
      directed: true,
      kinds: ["calls", "imports"],
    });
    expect(layer).toEqual({
      id: "path",
      nodeKeys: ["py:a.py:x:function", "py:b.py:y:function"],
      edgeKeys: ["py:a.py:x:function|py:b.py:y:function|calls"],
      style: "primary",
      label: "Path: x → y",
    });
  });

  it("clears an end", async () => {
    const user = userEvent.setup();
    renderWithApi(<Harness />);
    await choose(user, "Path from", "round_money", /round_money/);
    await user.click(screen.getByRole("button", { name: "Clear the from symbol" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Path from" })).toBeVisible());
  });
});
