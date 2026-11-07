"use client";

import type { Element, ElementContent } from "hast";
import { createContext, useContext, type ReactNode } from "react";
import ReactMarkdown, { type Components, type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Source } from "@/lib/api";
import { CITE_PREFIX, linkCitations, parseCitationHref, shortSource, sourcesOverlap } from "@/lib/citations";

/**
 * Answers are split into claims: each paragraph, list item and table row is one claim that knows the
 * citations inside it. The evidence rail (AnswerView) reads them back from the DOM through `data-*`
 * attributes, and these handlers let the chat react to hovering or clicking a claim.
 */
export interface ClaimHandlers {
  activeKey: string | null;
  linkedSources: Source[] | null;
  onHover: (key: string, sources: Source[], label: string) => void;
  onLeave: () => void;
  onSelect: (key: string, sources: Source[], label: string, index: number) => void;
}

interface MarkdownContext {
  onOpenSource: (source: Source) => void;
  claims: ClaimHandlers | null;
}

const Ctx = createContext<MarkdownContext>({ onOpenSource: () => undefined, claims: null });

const NESTED_BLOCKS = new Set(["p", "ul", "ol", "pre", "blockquote", "table", "h1", "h2", "h3", "h4", "h5", "h6"]);

function walkInline(nodes: ElementContent[], visit: (node: ElementContent) => void) {
  for (const node of nodes) {
    visit(node);
    if (node.type === "element" && !NESTED_BLOCKS.has(node.tagName)) walkInline(node.children, visit);
  }
}

function claimSources(node: Element): Source[] {
  const found: Source[] = [];
  walkInline(node.children, (child) => {
    if (child.type === "element" && child.tagName === "a") {
      const source = parseCitationHref(String(child.properties?.href ?? ""));
      if (source) found.push(source);
    }
  });
  return found;
}

function claimLabel(node: Element): string {
  const parts: string[] = [];
  const collect = (nodes: ElementContent[]) => {
    for (const child of nodes) {
      if (child.type === "text") parts.push(child.value);
      else if (child.type === "element" && !NESTED_BLOCKS.has(child.tagName)) {
        const isCitation = child.tagName === "a" && String(child.properties?.href ?? "").startsWith(CITE_PREFIX);
        if (!isCitation) collect(child.children);
      }
    }
  };
  collect(node.children);
  return parts.join("").replace(/\s+/g, " ").replace(/\s+([.,;:])/g, "$1").trim().slice(0, 160);
}

type BlockTag = "p" | "li" | "tr";

function makeClaimBlock(Tag: BlockTag) {
  function ClaimBlock({ node, children, ...rest }: React.HTMLAttributes<HTMLElement> & ExtraProps) {
    const { claims } = useContext(Ctx);
    const isContainer = node?.children.some((c) => c.type === "element" && (c.tagName === "p" || c.tagName === "th"));
    if (!claims || !node || isContainer) {
      return <Tag {...(rest as object)}>{children as ReactNode}</Tag>;
    }
    const key = `${node.position?.start.line}:${node.position?.start.column}`;
    const sources = claimSources(node);
    const label = claimLabel(node);
    const linked =
      !!claims.linkedSources &&
      sources.some((s) => claims.linkedSources!.some((l) => sourcesOverlap(s, l)));
    const className = [
      "claim",
      claims.activeKey === key ? "claim-active" : "",
      linked ? "claim-linked" : "",
      sources.length ? "" : "claim-uncited",
    ].join(" ");
    return (
      <Tag
        {...(rest as object)}
        className={className}
        data-claim={key}
        data-cited={sources.length}
        data-sources={JSON.stringify(sources)}
        data-label={label}
        onMouseEnter={() => claims.onHover(key, sources, label)}
        onMouseLeave={claims.onLeave}
        onClick={() => sources.length && claims.onSelect(key, sources, label, 0)}
      >
        {children as ReactNode}
      </Tag>
    );
  }
  ClaimBlock.displayName = `Claim(${Tag})`;
  return ClaimBlock;
}

function CitationLink({ href, children }: React.AnchorHTMLAttributes<HTMLAnchorElement> & ExtraProps) {
  const { onOpenSource, claims } = useContext(Ctx);
  const source = href ? parseCitationHref(href) : null;
  if (source) {
    return (
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          const block = event.currentTarget.closest<HTMLElement>("[data-claim]");
          if (claims && block?.dataset.claim) {
            const sources: Source[] = JSON.parse(block.dataset.sources || "[]");
            const index = Math.max(0, sources.findIndex((s) => s.path === source.path && s.start_line === source.start_line));
            claims.onSelect(block.dataset.claim, sources, block.dataset.label || "", index);
          } else {
            onOpenSource(source);
          }
        }}
        className="mx-0.5 inline rounded border border-border bg-code px-1.5 py-px align-baseline font-mono text-[11px] whitespace-nowrap text-accent no-underline hover:border-accent"
        title={`${source.path}:${source.start_line}${source.end_line !== source.start_line ? `-${source.end_line}` : ""}`}
      >
        {shortSource(source)}
      </button>
    );
  }
  const safe = href && !href.startsWith(CITE_PREFIX) && /^(https?:|mailto:|#)/.test(href) ? href : undefined;
  return (
    <a href={safe} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

const COMPONENTS: Components = {
  p: makeClaimBlock("p"),
  li: makeClaimBlock("li"),
  tr: makeClaimBlock("tr"),
  a: CitationLink,
};

export function Markdown({
  text,
  onOpenSource,
  claims = null,
}: {
  text: string;
  onOpenSource: (source: Source) => void;
  claims?: ClaimHandlers | null;
}) {
  return (
    <Ctx.Provider value={{ onOpenSource, claims }}>
      <div className="answer text-[14.5px]">
        <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={(url) => url} components={COMPONENTS}>
          {linkCitations(text)}
        </ReactMarkdown>
      </div>
    </Ctx.Provider>
  );
}
