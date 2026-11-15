"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, FlaskConical } from "lucide-react";
import Link from "next/link";
import { useApi } from "@/lib/api/context";
import type { GalleryItem } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

const COUNT = new Intl.NumberFormat("en-US");

function plural(count: number, one: string): string {
  return `${COUNT.format(count)} ${one}${count === 1 ? "" : "s"}`;
}

function Card({ item }: { item: GalleryItem }) {
  return (
    <li>
      <Link
        href={`/s/${item.snapshot_id}`}
        prefetch={false}
        aria-label={`Open the map of ${item.repo}`}
        className="group flex h-full flex-col rounded-xl border border-line bg-surface px-4 py-3.5 transition-colors hover:border-line-strong focus-visible:border-focus focus-visible:outline-none"
      >
        <span className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-semibold text-fg">{item.title}</span>
          <ArrowUpRight
            className="h-4 w-4 shrink-0 self-center text-fg-faint transition-colors group-hover:text-fg"
            aria-hidden="true"
          />
        </span>
        <span className="mt-0.5 truncate font-mono text-xs text-fg-faint">
          {item.repo}
          <span aria-hidden="true"> @ </span>
          <span className="sr-only"> at commit </span>
          {item.commit_sha.slice(0, 7)}
        </span>
        <span className="mt-2 flex-1 text-sm leading-relaxed text-fg-muted">
          {item.description}
        </span>
        <span className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-faint">
          <span>{item.language}</span>
          <span>{plural(item.files, "file")}</span>
          <span>{plural(item.nodes, "node")}</span>
          {item.benchmark ? (
            <span
              className="inline-flex items-center gap-1 text-fg-muted"
              title="One of the repositories the published accuracy numbers were measured on"
            >
              <FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />
              In the benchmark
            </span>
          ) : null}
        </span>
      </Link>
    </li>
  );
}

/**
 * Repositories precomputed on this deployment. Every card links straight to a ready snapshot,
 * so opening one never waits for a build. Renders nothing while loading, when the deployment
 * has no gallery, and when the gallery can't be read: the form above works without it.
 */
export function Gallery() {
  const api = useApi();
  const { data } = useQuery({
    queryKey: queryKeys.gallery(),
    queryFn: ({ signal }) => api.getGallery(signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const entries = data?.entries ?? [];
  if (!entries.length) return null;

  return (
    <section aria-labelledby="gallery-heading" className="mt-12">
      <h2 id="gallery-heading" className="text-sm font-semibold text-fg">
        Or open one that is already mapped
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        Built ahead of time at a pinned commit, so the map opens right away.
      </p>
      <ul className="mt-4 grid gap-3 sm:grid-cols-2">
        {entries.map((item) => (
          <Card key={item.snapshot_id} item={item} />
        ))}
      </ul>
    </section>
  );
}
