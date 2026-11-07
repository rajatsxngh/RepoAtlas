import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown, safeUrl } from "./markdown";

function html(markdown: string): HTMLElement {
  const { container } = render(<Markdown>{markdown}</Markdown>);
  return container;
}

describe("Markdown", () => {
  it("renders ordinary markdown", () => {
    const root = html(
      "# Title\n\nSome **bold** text with `code` and a [link](https://example.com).",
    );
    expect(root.querySelector("h1")?.textContent).toBe("Title");
    expect(root.querySelector("strong")?.textContent).toBe("bold");
    expect(root.querySelector("code")?.textContent).toBe("code");
    const link = root.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it.each([
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    '<iframe src="https://evil.example"></iframe>',
    '<a href="javascript:alert(1)">click</a>',
    '<div onclick="alert(1)">x</div>',
    "<style>body{display:none}</style>",
  ])("drops raw HTML: %s", (payload) => {
    const root = html(`Before\n\n${payload}\n\nAfter`);
    expect(root.querySelector("script, img, iframe, style, [onclick], [onerror]")).toBeNull();
    expect(root.textContent).toContain("Before");
    expect(root.textContent).toContain("After");
  });

  it("neutralizes dangerous link and image targets", () => {
    const root = html(
      "[a](javascript:alert(1)) [b](data:text/html,<script>alert(1)</script>) [c](vbscript:x) ![d](https://example.com/x.png)",
    );
    for (const link of root.querySelectorAll("a")) {
      expect(link.getAttribute("href") ?? "").not.toMatch(/^(javascript|data|vbscript):/i);
    }
    expect(root.querySelector("img")).toBeNull();
    expect(root.textContent).toContain("a");
  });

  it("keeps HTML-looking text inside code as text", () => {
    const root = html("```\n<script>alert(1)</script>\n```\n\nInline `<b>x</b>`");
    expect(root.querySelector("script, b")).toBeNull();
    expect(root.textContent).toContain("<script>alert(1)</script>");
    expect(root.textContent).toContain("<b>x</b>");
  });

  it("allows only http(s), mailto, and anchors", () => {
    expect(safeUrl("https://a.example")).toBe("https://a.example");
    expect(safeUrl("mailto:me@example.com")).toBe("mailto:me@example.com");
    expect(safeUrl("#section")).toBe("#section");
    expect(safeUrl(" JavaScript:alert(1)")).toBe("");
    expect(safeUrl("/relative")).toBe("");
  });
});
