import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ApiClient, type FetchLike } from "@/lib/api/client";
import { renderWithApi } from "@/test/render";
import { CodeViewer } from "./CodeViewer";
import { languageOf } from "./language";

describe("languageOf", () => {
  it.each([
    ["src/app.py", "python"],
    ["stubs/x.pyi", "python"],
    ["a.ts", "typescript"],
    ["a.mts", "typescript"],
    ["Button.tsx", "tsx"],
    ["a.js", "javascript"],
    ["a.cjs", "javascript"],
    ["App.jsx", "jsx"],
    ["README", "text"],
    ["weird.PY", "python"],
  ])("maps %s to %s", (path, language) => {
    expect(languageOf(path)).toBe(language);
  });
});

describe("CodeViewer", () => {
  it("shows numbered, highlighted lines from the snapshot", async () => {
    renderWithApi(
      <CodeViewer
        snapshotId={1}
        path="src/shop/cart.py"
        startLine={14}
        endLine={15}
        highlightRanges={[{ start: 14, end: 14 }]}
      />,
    );
    const figure = await screen.findByRole("figure", { name: "src/shop/cart.py, lines 14 to 15" });
    expect(figure).toHaveTextContent("def add(self, name: str, price: Decimal) -> None:");
    expect(figure).toHaveTextContent("lines 14–15 of 30");
    const marked = figure.querySelector('[data-line="14"]');
    expect(marked?.className).toContain("ra-code-mark");
    expect(figure.querySelector('[data-line="15"]')?.className).not.toContain("ra-code-mark");
    expect(figure.querySelector(".token.keyword")).not.toBeNull();
  });

  it("renders HTML and script in code as inert text", async () => {
    renderWithApi(
      <CodeViewer
        snapshotId={3}
        path="<img src=x onerror=alert('dir')>/<svg onload=alert('file')>.py"
        startLine={1}
        endLine={17}
      />,
    );
    const figure = await screen.findByRole("figure");
    expect(figure).toHaveTextContent("# <script>alert('comment')</script>");
    expect(figure).toHaveTextContent(
      `return helper("</textarea><script>alert('string')</script>")`,
    );
    expect(figure.querySelector("script, img, iframe, a, b, textarea")).toBeNull();
  });

  it("explains files whose text isn't stored", async () => {
    renderWithApi(<CodeViewer snapshotId={1} path="vendor/lib.py" startLine={1} endLine={5} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("skipped");
  });

  it("loads long ranges in pages of the API's line cap", async () => {
    const total = 950;
    const fetch: FetchLike = async (input) => {
      const url = new URL(input);
      const start = Number(url.searchParams.get("start"));
      const end = Math.min(Number(url.searchParams.get("end")), start + 399, total);
      const text = Array.from({ length: end - start + 1 }, (_, i) => `x = ${start + i}`).join("\n");
      return new Response(
        JSON.stringify({
          path: "big.py",
          blob_oid: "b".repeat(40),
          start,
          end,
          text,
          line_count: total,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };
    const api = new ApiClient({ baseUrl: "http://api.test/api/v1", fetch });
    const user = userEvent.setup();
    renderWithApi(<CodeViewer snapshotId={9} path="big.py" startLine={1} endLine={total} />, {
      api,
    });
    expect(await screen.findByText("lines 1–400 of 950")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show more lines" }));
    expect(await screen.findByText("lines 1–800 of 950")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show more lines" }));
    expect(await screen.findByText("lines 1–950 of 950")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Show more lines" })).toBeNull(),
    );
    expect(screen.getByRole("figure")).toHaveTextContent("x = 950");
  });
});
