"use client";

import { ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";

import { Markdown } from "@/components/Markdown";
import type { Source } from "@/lib/api";
import { sourcesOverlap } from "@/lib/citations";

export type ActivationVia = "hover" | "click" | "scroll";

interface RailDot {
  key: string;
  top: number;
  step: number; // 1-based among claims that cite code; 0 when the claim cites nothing
  sources: Source[];
  label: string;
}

interface Props {
  text: string;
  pending: boolean;
  activeKey: string | null;
  linkedSources: Source[] | null;
  scrollRoot: RefObject<HTMLDivElement | null>;
  onActivate: (key: string, sources: Source[], label: string, via: ActivationVia, index?: number) => void;
  onHoverClaim: (sources: Source[] | null) => void;
  onOpenSource: (source: Source) => void;
}

const HOVER_DWELL_MS = 140;

/** While streaming, hold back a citation whose closing bracket has not arrived yet. */
function hidePartialCitation(text: string): string {
  return text.replace(/\[[^\]\n]{0,300}$/, "");
}

/**
 * An answer with an evidence rail: every claim that cites code gets a numbered stop in the gutter.
 * Hovering, clicking, or (once the answer is complete) scrolling a claim into the middle of the view
 * shows its cited code in the evidence panel.
 */
export function AnswerView({ text, pending, activeKey, linkedSources, scrollRoot, onActivate, onHoverClaim, onOpenSource }: Props) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [dots, setDots] = useState<RailDot[]>([]);
  const dwell = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Measure claim positions after every layout change (streaming text, window resize).
  useEffect(() => {
    const root = contentRef.current;
    if (!root) return;
    const measure = () => {
      const base = root.getBoundingClientRect().top;
      let step = 0;
      const next = Array.from(root.querySelectorAll<HTMLElement>("[data-claim]")).map((el) => {
        const box = el.getBoundingClientRect();
        const cited = Number(el.dataset.cited) > 0;
        return {
          key: el.dataset.claim ?? "",
          top: Math.round(box.top - base + Math.min(12, box.height / 2)),
          step: cited ? ++step : 0,
          sources: JSON.parse(el.dataset.sources || "[]") as Source[],
          label: el.dataset.label ?? "",
        };
      });
      setDots((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    };
    const frame = requestAnimationFrame(measure);
    const observer = new ResizeObserver(() => measure());
    observer.observe(root);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [text]);

  const steps = useMemo(() => new Map(dots.map((d) => [d.key, d.step])), [dots]);
  const stepLabel = (key: string, label: string) => {
    const step = steps.get(key);
    return step ? `Step ${step}: ${label}` : label;
  };

  // Scroll-sync: once the answer is complete, the claim crossing the middle of the view drives the panel.
  useEffect(() => {
    const root = contentRef.current;
    const scroller = scrollRoot.current;
    if (pending || !root || !scroller) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const hit = entries.find((e) => e.isIntersecting)?.target as HTMLElement | undefined;
        if (!hit?.dataset.claim) return;
        const sources: Source[] = JSON.parse(hit.dataset.sources || "[]");
        const step = steps.get(hit.dataset.claim);
        const label = hit.dataset.label ?? "";
        onActivate(hit.dataset.claim, sources, step ? `Step ${step}: ${label}` : label, "scroll");
      },
      { root: scroller, rootMargin: "-42% 0px -48% 0px" },
    );
    root.querySelectorAll<HTMLElement>("[data-claim]").forEach((el) => {
      if (Number(el.dataset.cited) > 0) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [pending, text, scrollRoot, steps, onActivate]);

  useEffect(() => () => {
    if (dwell.current) clearTimeout(dwell.current);
  }, []);

  const cited = dots.filter((d) => d.step > 0);
  const files = new Set(cited.flatMap((d) => d.sources.map((s) => s.path)));
  const railTop = cited.length ? cited[0].top : 0;
  const railBottom = cited.length ? cited[cited.length - 1].top : 0;

  return (
    <div>
      <div className="relative pl-8">
        <div className="pointer-events-none absolute inset-y-0 left-0 w-8" aria-hidden={cited.length === 0}>
          {cited.length > 1 && (
            <div className="absolute left-[11px] w-px bg-border" style={{ top: railTop, height: railBottom - railTop }} />
          )}
          {dots.map((dot) => {
            const active = dot.key === activeKey;
            const linked = !!linkedSources && dot.sources.some((s) => linkedSources.some((l) => sourcesOverlap(s, l)));
            if (!dot.step) {
              return (
                <span
                  key={dot.key}
                  title="This point cites no code"
                  className="absolute left-[8px] size-[7px] -translate-y-1/2 rounded-full border border-muted/60 bg-background"
                  style={{ top: dot.top }}
                />
              );
            }
            // A small stop on the rail; the active one opens into its step number (matching the panel).
            return (
              <button
                key={dot.key}
                type="button"
                onClick={() => onActivate(dot.key, dot.sources, stepLabel(dot.key, dot.label), "click")}
                title={`Step ${dot.step}: show the cited code`}
                aria-label={`Step ${dot.step}`}
                className="group pointer-events-auto absolute left-[1px] flex size-[21px] -translate-y-1/2 items-center justify-center"
                style={{ top: dot.top }}
              >
                <span
                  className={`flex items-center justify-center rounded-full text-[10px] font-medium tabular-nums transition-all duration-200 ${
                    active
                      ? "size-[19px] bg-accent text-white dark:text-black"
                      : linked
                        ? "size-[13px] bg-accent ring-4 ring-accent-soft"
                        : "size-[9px] bg-accent/45 group-hover:size-[13px] group-hover:bg-accent"
                  }`}
                >
                  {active ? dot.step : null}
                </span>
              </button>
            );
          })}
        </div>
        <div ref={contentRef}>
          <Markdown
            text={pending ? hidePartialCitation(text) : text}
            onOpenSource={onOpenSource}
            claims={{
              activeKey,
              linkedSources,
              onHover: (key, sources, label) => {
                onHoverClaim(sources.length ? sources : null);
                if (dwell.current) clearTimeout(dwell.current);
                if (sources.length) {
                  dwell.current = setTimeout(() => onActivate(key, sources, stepLabel(key, label), "hover"), HOVER_DWELL_MS);
                }
              },
              onLeave: () => {
                if (dwell.current) clearTimeout(dwell.current);
                onHoverClaim(null);
              },
              onSelect: (key, sources, label, index) => onActivate(key, sources, stepLabel(key, label), "click", index),
            }}
          />
        </div>
      </div>
      {!pending && dots.length > 0 && (
        <p className="mt-3 flex items-center gap-1.5 pl-8 text-[12px] text-muted">
          <ShieldCheck className={`size-3.5 ${cited.length === dots.length ? "text-ok" : ""}`} aria-hidden />
          {cited.length} of {dots.length} points cite code
          {files.size > 0 && ` · ${files.size} ${files.size === 1 ? "file" : "files"}`}
          {cited.length < dots.length && (
            <span className="ml-1 inline-flex items-center gap-1">
              · <span className="inline-block size-[7px] rounded-full border border-muted/60" /> = no citation
            </span>
          )}
        </p>
      )}
    </div>
  );
}
