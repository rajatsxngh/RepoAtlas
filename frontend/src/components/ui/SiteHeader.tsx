import Link from "next/link";
import type { ReactNode } from "react";
import { Wordmark } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function SiteHeader({ children }: { children?: ReactNode }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 px-5">
      <Link href="/" className="rounded-lg" aria-label="RepoAtlas home">
        <Wordmark />
      </Link>
      <div className="flex min-w-0 flex-1 items-center gap-3">{children}</div>
      <ThemeToggle />
    </header>
  );
}
