/**
 * Markdown for anything the app shows from repos or models. Raw HTML is dropped (never parsed),
 * the tree is sanitized with a strict allow-list, and links may only point to http(s) or mailto.
 * React renders what is left as elements and text, so no input can inject markup or scripts.
 */

import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize, { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

const SAFE_PROTOCOL = /^(https?:|mailto:)/i;

/** Only http(s), mailto, and in-page anchors survive; everything else becomes inert. */
export function safeUrl(url: string): string {
  const trimmed = url.trim();
  if (trimmed.startsWith("#")) return trimmed;
  return SAFE_PROTOCOL.test(trimmed) ? trimmed : "";
}

/** GitHub's schema without images, inputs, or anything that loads or submits. */
const SCHEMA: SanitizeSchema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter(
    (tag) => !["img", "input", "picture", "source", "video", "audio", "iframe"].includes(tag),
  ),
  protocols: { ...defaultSchema.protocols, href: ["http", "https", "mailto"] },
};

const COMPONENTS: Components = {
  a: ({
    node: _node,
    href,
    children,
    ...rest
  }: ComponentPropsWithoutRef<"a"> & { node?: unknown }) => {
    const target = href ? safeUrl(href) : "";
    if (!target) return <span>{children}</span>;
    return (
      <a
        {...rest}
        href={target}
        target={target.startsWith("#") ? undefined : "_blank"}
        rel="noopener noreferrer nofollow"
        className="text-focus underline underline-offset-2"
      >
        {children}
      </a>
    );
  },
  code: ({
    node: _node,
    className,
    ...rest
  }: ComponentPropsWithoutRef<"code"> & { node?: unknown }) => (
    <code
      {...rest}
      className={`rounded bg-surface-2 px-1 py-0.5 font-mono text-[0.9em] ${className ?? ""}`}
    />
  ),
};

export interface MarkdownProps {
  children: string;
  className?: string;
}

export function Markdown({ children, className = "" }: MarkdownProps) {
  return (
    <div className={`ra-markdown space-y-2 text-sm leading-relaxed text-fg ${className}`}>
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, SCHEMA]]}
        urlTransform={safeUrl}
        components={COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
