"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Source } from "@/lib/api";
import { CITE_PREFIX, linkCitations, parseCitationHref } from "@/lib/citations";

export function Markdown({ text, onOpenSource }: { text: string; onOpenSource: (source: Source) => void }) {
  return (
    <div className="answer text-[14.5px]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => url}
        components={{
          a: ({ href, children }) => {
            const source = href ? parseCitationHref(href) : null;
            if (source) {
              return (
                <button
                  type="button"
                  onClick={() => onOpenSource(source)}
                  className="mx-0.5 inline-flex items-center rounded border border-border bg-code px-1.5 py-px align-baseline font-mono text-[11.5px] text-accent no-underline hover:border-accent"
                  title="Open in code viewer"
                >
                  {children}
                </button>
              );
            }
            const safe = href && !href.startsWith(CITE_PREFIX) && /^(https?:|mailto:|#)/.test(href) ? href : undefined;
            return (
              <a href={safe} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}
