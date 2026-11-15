import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { GalleryItem } from "@/lib/api/types";
import { mockApi, renderWithApi } from "@/test/render";
import { Gallery } from "./Gallery";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    prefetch: _prefetch,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    prefetch?: boolean;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function item(changes: Partial<GalleryItem> = {}): GalleryItem {
  return {
    snapshot_id: 7,
    repo: "pallets/flask",
    commit_sha: "49943b5a94413a530761350f04e896f76e8d620e",
    title: "Flask",
    description: "A small web framework.",
    language: "Python",
    license: "BSD-3-Clause",
    benchmark: false,
    files: 83,
    nodes: 1204,
    edges: 3310,
    ...changes,
  };
}

describe("Gallery", () => {
  it("links each precomputed repository straight to its snapshot", async () => {
    renderWithApi(<Gallery />);
    const section = await screen.findByRole("region", {
      name: "Or open one that is already mapped",
    });
    const cards = within(section).getAllByRole("listitem");
    expect(cards).toHaveLength(2);
    const python = within(cards[0]!).getByRole("link", {
      name: "Open the map of repoatlas-fixtures/py-small",
    });
    expect(python).toHaveAttribute("href", "/s/1");
    expect(within(cards[0]!).getByText("Shop (Python)")).toBeVisible();
    expect(within(cards[0]!).getByText("In the benchmark")).toBeVisible();
    expect(within(cards[1]!).getByRole("link")).toHaveAttribute("href", "/s/2");
    expect(within(cards[1]!).queryByText("In the benchmark")).not.toBeInTheDocument();
  });

  it("shows the pinned commit and sizes, with separators and singulars", async () => {
    const { api } = mockApi();
    vi.spyOn(api, "getGallery").mockResolvedValue({
      entries: [item(), item({ snapshot_id: 8, repo: "a/b", title: "B", files: 1, nodes: 1 })],
    });
    renderWithApi(<Gallery />, { api });
    const [flask, tiny] = await screen.findAllByRole("listitem");
    expect(within(flask!).getByText(/pallets\/flask/)).toHaveTextContent(
      "pallets/flask @ at commit 49943b5",
    );
    expect(within(flask!).getByText("83 files")).toBeVisible();
    expect(within(flask!).getByText("1,204 nodes")).toBeVisible();
    expect(within(tiny!).getByText("1 file")).toBeVisible();
    expect(within(tiny!).getByText("1 node")).toBeVisible();
  });

  it("shows card text as text, never as markup", async () => {
    const { api } = mockApi();
    vi.spyOn(api, "getGallery").mockResolvedValue({
      entries: [item({ title: "<img src=x onerror=alert(1)>", description: "<b>bold</b>" })],
    });
    const { container } = renderWithApi(<Gallery />, { api });
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(screen.getByText("<b>bold</b>")).toBeVisible();
    expect(container.querySelector("img, b")).toBeNull();
  });

  it("renders nothing for an empty gallery or one that can't be read", async () => {
    const empty = mockApi();
    const read = vi.spyOn(empty.api, "getGallery").mockResolvedValue({ entries: [] });
    const first = renderWithApi(<Gallery />, { api: empty.api });
    await waitFor(() => expect(read).toHaveBeenCalled());
    expect(first.container).toBeEmptyDOMElement();
    first.unmount();

    const broken = mockApi();
    const failed = vi
      .spyOn(broken.api, "getGallery")
      .mockRejectedValue(new ApiError(500, "internal", "boom"));
    const second = renderWithApi(<Gallery />, { api: broken.api });
    await waitFor(() => expect(failed).toHaveBeenCalled());
    expect(second.container).toBeEmptyDOMElement();
  });
});
