"use client";

/**
 * Lines of a file from the snapshot, with syntax highlighting. Code is repo content and
 * untrusted: Prism splits it into tokens that are rendered as React text, so nothing in it can
 * become HTML. Long ranges load in pages of the API's line cap.
 */

import { useInfiniteQuery } from "@tanstack/react-query";
import { Highlight, type PrismTheme } from "prism-react-renderer";
import { useMemo } from "react";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { CodeSlice } from "@/lib/api/types";
import { languageOf } from "./language";

export interface LineRange {
  start: number;
  end: number;
}

export interface CodeViewerProps {
  snapshotId: number;
  path: string;
  startLine: number;
  endLine: number;
  /** Lines to mark (for example a symbol's name or a cited range). */
  highlightRanges?: LineRange[];
  /** Extra classes for the scrolling area. */
  className?: string;
}

/** Colors come from CSS (`.token.*` with theme variables), so both themes share one palette. */
const THEME: PrismTheme = { plain: {}, styles: [] };

function marked(line: number, ranges: readonly LineRange[]): boolean {
  return ranges.some((range) => line >= range.start && line <= range.end);
}

export function CodeViewer({
  snapshotId,
  path,
  startLine,
  endLine,
  highlightRanges = [],
  className = "",
}: CodeViewerProps) {
  const api = useApi();
  const first = Math.max(1, startLine);
  const last = Math.max(first, endLine);
  const pages = useInfiniteQuery({
    queryKey: ["code", snapshotId, path, first, last],
    initialPageParam: first,
    queryFn: ({ pageParam, signal }) =>
      api.readCode(snapshotId, { path, start: pageParam, end: last }, signal),
    getNextPageParam: (page: CodeSlice) =>
      page.end < Math.min(last, page.line_count) ? page.end + 1 : undefined,
  });

  const slices = useMemo(() => pages.data?.pages ?? [], [pages.data]);
  const code = useMemo(() => slices.map((slice) => slice.text).join("\n"), [slices]);
  const language = languageOf(path);
  const shownEnd = slices.at(-1)?.end ?? first;
  const lineCount = slices[0]?.line_count;

  if (pages.isPending) {
    return (
      <div className="flex items-center gap-2 px-4 py-6 text-sm text-fg-muted" role="status">
        <Spinner /> Loading code…
      </div>
    );
  }
  if (pages.isError) {
    const { title, detail } = describeError(pages.error);
    return (
      <div className="px-4 py-4 text-sm" role="alert">
        <p className="font-medium text-fg">{title}</p>
        <p className="mt-0.5 text-fg-muted">{detail}</p>
      </div>
    );
  }

  return (
    <figure
      className="ra-code overflow-hidden rounded-xl border border-line bg-surface"
      aria-label={`${path}, lines ${first} to ${shownEnd}`}
    >
      <figcaption className="flex items-center gap-2 border-b border-line bg-surface-2 px-3 py-1.5 text-[11px] text-fg-muted">
        <span className="min-w-0 flex-1 truncate font-mono" title={path}>
          {path}
        </span>
        <span className="shrink-0 tabular-nums">
          {first === shownEnd ? `line ${first}` : `lines ${first}–${shownEnd}`}
          {lineCount ? ` of ${lineCount}` : null}
        </span>
      </figcaption>
      <div className={`overflow-auto ${className}`}>
        <Highlight theme={THEME} code={code} language={language === "text" ? "markup" : language}>
          {({ tokens, getTokenProps }) => (
            <pre className="min-w-max py-2 font-mono text-[12.5px] leading-[1.6]">
              <code>
                {tokens.map((line, index) => {
                  const number = first + index;
                  const highlighted = marked(number, highlightRanges);
                  return (
                    <div
                      key={number}
                      className={`flex pr-4 ${highlighted ? "ra-code-mark" : ""}`}
                      data-line={number}
                    >
                      <span
                        className="w-12 shrink-0 pr-3 text-right text-fg-faint select-none"
                        aria-hidden="true"
                      >
                        {number}
                      </span>
                      <span className="whitespace-pre">
                        {language === "text"
                          ? line.map((token) => (token.empty ? "" : token.content)).join("")
                          : line
                              .filter((token) => !token.empty)
                              .map((token, position) => (
                                <span key={position} {...getTokenProps({ token })} />
                              ))}
                      </span>
                    </div>
                  );
                })}
              </code>
            </pre>
          )}
        </Highlight>
      </div>
      {pages.hasNextPage ? (
        <div className="border-t border-line px-3 py-2">
          <Button
            size="sm"
            onClick={() => void pages.fetchNextPage()}
            disabled={pages.isFetchingNextPage}
            icon={pages.isFetchingNextPage ? <Spinner className="h-3.5 w-3.5" /> : null}
          >
            Show more lines
          </Button>
        </div>
      ) : null}
    </figure>
  );
}
