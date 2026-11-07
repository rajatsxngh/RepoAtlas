import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { parseSnapshotId } from "@/lib/snapshotId";
import { mockApi, renderWithApi } from "@/test/render";
import { SnapshotGate } from "./SnapshotGate";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const ready = (snapshot: { repo: { name: string } }) => <p>Map of {snapshot.repo.name}</p>;

describe("SnapshotGate", () => {
  it("renders the workspace for a ready snapshot", async () => {
    renderWithApi(<SnapshotGate snapshotId={1}>{ready}</SnapshotGate>);
    expect(await screen.findByText("Map of py-small")).toBeVisible();
  });

  it("follows a running build, then renders the workspace", async () => {
    const { api } = mockApi();
    await api.buildRepo("https://github.com/repoatlas-fixtures/ts-small");
    renderWithApi(
      <SnapshotGate snapshotId={2} pollMs={15}>
        {ready}
      </SnapshotGate>,
      { api },
    );
    expect(await screen.findByRole("heading", { name: /Building the map/ })).toBeVisible();
    expect(await screen.findByText("Map of ts-small", {}, { timeout: 3000 })).toBeVisible();
  });

  it("offers to rebuild a failed snapshot", async () => {
    const user = userEvent.setup();
    renderWithApi(
      <SnapshotGate snapshotId={5} pollMs={15}>
        {ready}
      </SnapshotGate>,
    );
    expect(
      await screen.findByRole("heading", { name: "This map couldn't be built" }),
    ).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("repoatlas-fixtures/broken");
    await user.click(screen.getByRole("button", { name: "Build it again" }));
    expect(await screen.findByRole("heading", { name: /Building the map/ })).toBeVisible();
  });

  it("says when a snapshot doesn't exist", async () => {
    renderWithApi(<SnapshotGate snapshotId={404}>{ready}</SnapshotGate>);
    expect(await screen.findByRole("heading", { name: "There's no map here" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Map a repository" })).toHaveAttribute("href", "/");
  });
});

describe("parseSnapshotId", () => {
  it.each([
    ["1", 1],
    ["42", 42],
    ["0", null],
    ["007", null],
    ["-1", null],
    ["1e3", null],
    ["99999999999999999", null],
    ["abc", null],
  ])("parses %j as %j", (raw, id) => {
    expect(parseSnapshotId(raw)).toBe(id);
  });
});
