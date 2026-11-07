"use client";

import { ArrowRight, CircleCheck, Link2 } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { checkRepoUrl } from "@/lib/repoUrl";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

export interface RepoFormProps {
  /** Called with a URL that passed the client-side checks. */
  onSubmit: (url: string) => void;
  busy?: boolean;
  /** Seconds until another build request is allowed (rate limited). */
  waitS?: number;
  examples?: readonly string[];
  initialValue?: string;
}

export function RepoForm({
  onSubmit,
  busy = false,
  waitS = 0,
  examples = [],
  initialValue = "",
}: RepoFormProps) {
  const [value, setValue] = useState(initialValue);
  const [touched, setTouched] = useState(false);
  const hintId = useId();
  const inputId = useId();
  const check = checkRepoUrl(value);
  const showProblem = touched && !check.ok && value.trim() !== "";
  const blocked = busy || waitS > 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (check.ok && !blocked) onSubmit(check.url);
  }

  function use(url: string) {
    setValue(url);
    setTouched(true);
    if (!blocked) onSubmit(url);
  }

  return (
    <form onSubmit={submit} noValidate className="w-full">
      <label htmlFor={inputId} className="sr-only">
        GitHub repository URL
      </label>
      <div
        className={`flex items-center gap-2 rounded-2xl border bg-surface p-2 pl-4 shadow-card transition-colors focus-within:border-focus ${
          showProblem ? "border-danger" : "border-line"
        }`}
      >
        <Link2 className="h-5 w-5 shrink-0 text-fg-faint" aria-hidden="true" />
        <input
          id={inputId}
          name="url"
          type="url"
          inputMode="url"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="https://github.com/owner/repository"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onBlur={() => setTouched(true)}
          aria-invalid={showProblem}
          aria-describedby={hintId}
          className="h-10 min-w-0 flex-1 bg-transparent text-base text-fg outline-none placeholder:text-fg-faint"
          disabled={busy}
        />
        <Button
          type="submit"
          variant="primary"
          size="md"
          disabled={blocked || value.trim() === ""}
          className="h-10 rounded-xl px-4"
          icon={busy ? <Spinner /> : null}
        >
          {busy ? "Starting" : waitS > 0 ? `Wait ${waitS} s` : "Map it"}
          {busy || waitS > 0 ? null : <ArrowRight className="h-4 w-4" aria-hidden="true" />}
        </Button>
      </div>
      <div id={hintId} className="mt-2.5 min-h-5 px-1 text-sm" aria-live="polite">
        {showProblem && !check.ok ? (
          <span className="text-danger">
            {check.message}{" "}
            {check.suggestion ? (
              <button
                type="button"
                className="cursor-pointer font-medium underline underline-offset-2 hover:text-fg"
                onClick={() => use(check.suggestion!)}
              >
                Use {check.suggestion}
              </button>
            ) : null}
          </span>
        ) : check.ok ? (
          <span className="inline-flex items-center gap-1.5 text-fg-muted">
            <CircleCheck className="h-4 w-4 text-ok" aria-hidden="true" />
            github.com/{check.owner}/{check.name}
          </span>
        ) : (
          <span className="text-fg-muted">
            Any public GitHub repository with Python, TypeScript, or JavaScript code.
          </span>
        )}
      </div>
      {examples.length ? (
        <div className="mt-5 flex flex-wrap items-center gap-2 px-1">
          <span className="text-xs text-fg-faint">Try</span>
          {examples.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => use(`https://github.com/${example}`)}
              disabled={blocked}
              className="cursor-pointer rounded-full border border-line bg-surface px-3 py-1 font-mono text-xs text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
            >
              {example}
            </button>
          ))}
        </div>
      ) : null}
    </form>
  );
}
