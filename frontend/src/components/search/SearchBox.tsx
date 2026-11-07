"use client";

/**
 * Finds symbols by name, qualified name, or key (`/search`) as you type, and hands the picked
 * node to the caller (the map reveals and focuses it). A combobox: arrow keys move, Enter picks,
 * Escape closes, and "/" focuses the box from anywhere on the page.
 */

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { KindIcon } from "@/components/graph/KindIcon";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { ApiNode, NodeKind, SymbolMatch } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

export interface SearchBoxProps {
  snapshotId: number;
  onPick: (node: ApiNode) => void;
  /** Only these kinds (default: all). */
  kinds?: NodeKind[];
  label?: string;
  placeholder?: string;
  /** Focus the box when "/" is pressed outside a text field. */
  slashShortcut?: boolean;
  debounceMs?: number;
  limit?: number;
}

/** The text with the first case-insensitive occurrence of `needle` marked, as text nodes. */
export function markMatch(text: string, needle: string): ReactNode {
  const at = needle ? text.toLowerCase().indexOf(needle.toLowerCase()) : -1;
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded-sm bg-focus/20 text-inherit">
        {text.slice(at, at + needle.length)}
      </mark>
      {text.slice(at + needle.length)}
    </>
  );
}

function secondary(node: ApiNode): string {
  if (node.kind === "dir" || node.kind === "file") return node.path;
  const where = node.span ? `${node.path}:${node.span.start_line}` : node.path;
  return node.qualified_name !== node.name ? `${node.qualified_name} · ${where}` : where;
}

export function SearchBox({
  snapshotId,
  onPick,
  kinds = [],
  label = "Search symbols",
  placeholder = "Search functions, classes, files…",
  slashShortcut = false,
  debounceMs = 200,
  limit = 20,
}: SearchBoxProps) {
  const api = useApi();
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(text.trim());
      setActive(0);
    }, debounceMs);
    return () => clearTimeout(timer);
  }, [text, debounceMs]);

  useEffect(() => {
    if (!slashShortcut) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "");
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [slashShortcut]);

  const search = useQuery({
    enabled: query.length > 0,
    queryKey: queryKeys.search(snapshotId, query, kinds),
    queryFn: ({ signal }) =>
      api.search(snapshotId, { q: query, kinds: kinds.length ? kinds : null, limit }, signal),
  });
  const results: SymbolMatch[] = query && search.data?.query === query ? search.data.results : [];
  const showList = open && text.trim().length > 0;
  const activeId = results[active] ? `${listId}-${active}` : undefined;

  const pick = (match: SymbolMatch | undefined) => {
    if (!match) return;
    onPick(match.node);
    setOpen(false);
    input.current?.blur();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((index) => Math.min(index + 1, Math.max(results.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      pick(results[active]);
    } else if (event.key === "Escape") {
      if (showList) setOpen(false);
      else setText("");
    }
  };

  return (
    <div className="relative w-full">
      <label className="sr-only" htmlFor={`${listId}-input`}>
        {label}
      </label>
      <div className="flex h-9 items-center gap-2 rounded-lg border border-line bg-surface px-2.5 transition-colors focus-within:border-focus">
        <Search className="h-4 w-4 shrink-0 text-fg-faint" aria-hidden="true" />
        <input
          ref={input}
          id={`${listId}-input`}
          type="search"
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList ? activeId : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder={placeholder}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          className="h-full min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-faint [&::-webkit-search-cancel-button]:hidden"
        />
        {search.isFetching ? <Spinner className="h-3.5 w-3.5 text-fg-faint" /> : null}
        {slashShortcut && !text ? (
          <kbd className="rounded border border-line px-1.5 font-mono text-[10px] text-fg-faint">
            /
          </kbd>
        ) : null}
      </div>
      {showList ? (
        <div className="absolute inset-x-0 top-full z-40 mt-1.5 overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
          <ul
            id={listId}
            role="listbox"
            aria-label="Matching symbols"
            className="max-h-96 overflow-y-auto p-1"
          >
            {results.map((match, index) => (
              <li
                key={match.node.key}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                className={`flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-1.5 ${
                  index === active ? "bg-surface-2" : ""
                }`}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActive(index)}
                onClick={() => pick(match)}
              >
                <KindIcon kind={match.node.kind} className="mt-0.5 h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-fg">
                    {markMatch(match.node.name, query)}
                  </span>
                  <span className="block truncate font-mono text-[11px] text-fg-faint">
                    {secondary(match.node)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          {query && !search.isFetching && !search.isError && results.length === 0 ? (
            <p className="px-3 py-2.5 text-[13px] text-fg-muted">No symbols match “{query}”.</p>
          ) : null}
          {search.isError ? (
            <p className="px-3 py-2.5 text-[13px] text-danger" role="alert">
              {describeError(search.error).detail}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
