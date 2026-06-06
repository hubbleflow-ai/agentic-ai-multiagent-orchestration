"use client";

/**
 * Shared markdown renderer for any LLM-emitted text in the UI.
 * Ported verbatim from S5 · single source of truth for "how agent text looks".
 *
 *   <Markdown>{message.content}</Markdown>
 *   <Markdown variant="reasoning">{turn.reasoning}</Markdown>
 *
 * Built on `react-markdown` + `remark-gfm` (GitHub-flavoured markdown:
 * tables, task lists, strikethrough, autolinks). Per-element styling via
 * the `components` prop so the output matches our Tailwind tokens without
 * needing `@tailwindcss/typography`.
 *
 * Variants:
 *   - "response"   · user-facing reply (15px body)
 *   - "reasoning"  · thinking panel (12.5px, muted)
 */

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

type Variant = "response" | "reasoning";

type Props = {
  children: string;
  variant?: Variant;
  className?: string;
};

export function Markdown({ children, variant = "response", className = "" }: Props) {
  return (
    <div className={`${rootClass[variant]} ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mapComponents(variant)}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

const rootClass: Record<Variant, string> = {
  response:
    "text-[15px] leading-relaxed text-ink [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
  reasoning:
    "text-[12.5px] leading-relaxed text-muted [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
};

function mapComponents(variant: Variant): Components {
  const codeText =
    variant === "reasoning"
      ? "font-mono text-[11.5px] text-ink/85"
      : "font-mono text-[13px] text-ink";
  const codeBg = "rounded bg-surfaceMuted px-1.5 py-0.5 border border-edge";

  return {
    p: ({ children }) => <p className="my-2 whitespace-pre-wrap">{children}</p>,

    /* headings */
    h1: ({ children }) => (
      <h1 className="mb-2 mt-4 text-[1.15em] font-semibold text-ink">{children}</h1>
    ),
    h2: ({ children }) => (
      <h2 className="mb-2 mt-3 text-[1.08em] font-semibold text-ink">{children}</h2>
    ),
    h3: ({ children }) => (
      <h3 className="mb-1.5 mt-3 text-[1em] font-semibold text-ink">{children}</h3>
    ),
    h4: ({ children }) => (
      <h4 className="mb-1 mt-2 text-[0.95em] font-semibold text-ink">{children}</h4>
    ),

    /* inline */
    strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
      >
        {children}
      </a>
    ),

    /* code · block vs inline */
    code: ({ className: cls, children, ...rest }) => {
      const isBlock = /language-/.test(cls ?? "");
      if (isBlock) {
        return (
          <code className={`${cls ?? ""} block`} {...rest}>
            {children}
          </code>
        );
      }
      return (
        <code className={`${codeBg} ${codeText}`} {...rest}>
          {children}
        </code>
      );
    },
    pre: ({ children }) => (
      <pre className="my-2 overflow-x-auto rounded-lg border border-edge bg-surfaceMuted p-3 font-mono text-[12.5px] text-ink/90">
        {children}
      </pre>
    ),

    /* lists */
    ul: ({ children }) => (
      <ul className="my-2 list-disc space-y-1 pl-5 marker:text-muted">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-muted">{children}</ol>
    ),
    li: ({ children }) => <li className="leading-relaxed">{children}</li>,

    /* quote + divider */
    blockquote: ({ children }) => (
      <blockquote className="my-2 border-l-2 border-edge pl-3 italic text-muted">
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-3 border-edge" />,

    /* tables (GFM) */
    table: ({ children }) => (
      <div className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-left text-[13px]">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="border-b border-edge">{children}</thead>,
    tr: ({ children }) => (
      <tr className="border-b border-edge/60 last:border-0">{children}</tr>
    ),
    th: ({ children }) => (
      <th className="px-2 py-1.5 font-semibold text-ink">{children}</th>
    ),
    td: ({ children }) => <td className="px-2 py-1.5 text-ink/90">{children}</td>,
  };
}

/** Convenience wrapper: renders nothing for non-string / empty input. */
export function MaybeMarkdown({
  children,
  ...rest
}: {
  children: unknown;
  variant?: Variant;
  className?: string;
}): ReactNode {
  if (typeof children !== "string" || children.length === 0) return null;
  return <Markdown {...rest}>{children}</Markdown>;
}
