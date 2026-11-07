import { GitBranch, Layers, ShieldCheck } from "lucide-react";
import { Landing } from "@/components/landing/Landing";
import { SiteHeader } from "@/components/ui/SiteHeader";

const POINTS = [
  {
    icon: Layers,
    title: "Starts small, opens on demand",
    text: "Top-level folders first. Open folders into files and files into classes and functions.",
  },
  {
    icon: GitBranch,
    title: "Edges you can trust",
    text: "Imports, calls, inheritance, and tests. Resolved links are solid; name-matched guesses are dashed.",
  },
  {
    icon: ShieldCheck,
    title: "Read, never run",
    text: "Code comes from the exact commit and is shown as plain text. Nothing from the repo executes.",
  },
];

export default function HomePage() {
  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 [background-image:radial-gradient(var(--ra-line-strong)_1px,transparent_1px)] [mask-image:radial-gradient(ellipse_at_50%_35%,black_20%,transparent_70%)] [background-size:22px_22px] opacity-60"
      />
      <SiteHeader />
      <main className="relative mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-5 pt-10 pb-20">
        <p className="mb-4 text-sm font-medium text-fg-muted">Code maps from static analysis</p>
        <h1 className="text-4xl font-semibold tracking-tight text-balance text-fg sm:text-5xl">
          See how a repository fits together
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-pretty text-fg-muted">
          Paste a public GitHub URL. RepoAtlas parses the code, links imports and calls, and opens
          it as a map you can explore — with the source one click away.
        </p>
        <div className="mt-9">
          <Landing />
        </div>
        <ul className="mt-14 grid gap-6 sm:grid-cols-3">
          {POINTS.map(({ icon: Icon, title, text }) => (
            <li key={title}>
              <Icon className="h-5 w-5 text-fg-faint" aria-hidden="true" />
              <h2 className="mt-2.5 text-sm font-semibold text-fg">{title}</h2>
              <p className="mt-1 text-sm leading-relaxed text-fg-muted">{text}</p>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
