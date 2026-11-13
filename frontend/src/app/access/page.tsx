import type { Metadata } from "next";
import { Wordmark } from "@/components/ui/Logo";
import { ACCESS_ENTER_PATH, safeNext } from "@/lib/access";

export const metadata: Metadata = {
  title: "Private preview",
  robots: { index: false, follow: false },
};

type Search = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/** The one-time token entry of a private deployment (see `src/lib/access.ts`). */
export default async function AccessPage({ searchParams }: { searchParams: Search }) {
  const params = await searchParams;
  const next = safeNext(first(params.next));
  const failed = first(params.error) !== undefined;
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-sm flex-col justify-center px-5 py-16">
      <Wordmark />
      <h1 className="mt-8 text-2xl font-semibold tracking-tight text-fg">Private preview</h1>
      <p className="mt-2 text-sm leading-relaxed text-fg-muted">
        This RepoAtlas deployment isn&apos;t public yet. Enter the access token you were given.
      </p>
      <form method="post" action={ACCESS_ENTER_PATH} className="mt-6 flex flex-col gap-3">
        <input type="hidden" name="next" value={next} />
        <label htmlFor="access-token" className="text-sm font-medium text-fg">
          Access token
        </label>
        <input
          id="access-token"
          name="token"
          type="password"
          required
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-invalid={failed}
          aria-describedby={failed ? "access-error" : undefined}
          className="h-11 rounded-lg border border-line bg-surface px-3 text-sm text-fg outline-none focus:border-line-strong"
        />
        {failed ? (
          <p id="access-error" role="alert" className="text-sm text-danger">
            That token isn&apos;t right. Check it and try again.
          </p>
        ) : null}
        <button
          type="submit"
          className="mt-1 inline-flex h-11 cursor-pointer items-center justify-center rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg hover:opacity-90"
        >
          Continue
        </button>
      </form>
    </main>
  );
}
