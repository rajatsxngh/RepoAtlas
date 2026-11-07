/** The RepoAtlas mark: three linked nodes in a rounded tile. */
export function LogoMark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="var(--ra-accent)" />
      <path
        d="M10 21.5 16 11l6 10.5"
        fill="none"
        stroke="var(--ra-accent-fg)"
        strokeOpacity="0.55"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="16" cy="10.5" r="3.2" fill="var(--ra-hl-primary)" />
      <circle cx="9.5" cy="22" r="3.2" fill="var(--ra-accent-fg)" />
      <circle cx="22.5" cy="22" r="3.2" fill="var(--ra-accent-fg)" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-tight text-fg">RepoAtlas</span>
    </span>
  );
}
