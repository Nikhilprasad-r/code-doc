"use client";

import {
  Boxes,
  Braces,
  ChevronRight,
  FileText,
  Loader2,
  Map as MapIcon,
  Network,
  Package,
  Radar,
  Regex,
  Route,
  Search,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import type { Source } from "@/lib/api";
import { dedupeSources, shortSource, sourcesOverlap } from "@/lib/citations";
import { formatMs } from "@/lib/format";

export interface ToolStep {
  name: string;
  input?: Record<string, unknown>;
  summary?: string;
  sources: Source[];
  isError?: boolean;
  /** How long the tool took on the server. */
  ms?: number;
  done: boolean;
}

const TOOLS: Record<string, { icon: LucideIcon; working: string }> = {
  repo_overview: { icon: MapIcon, working: "Reading the overview" },
  search_code: { icon: Search, working: "Searching" },
  read_file: { icon: FileText, working: "Reading a file" },
  grep: { icon: Regex, working: "Grepping" },
  find_symbol: { icon: Braces, working: "Looking up a symbol" },
  list_endpoints: { icon: Route, working: "Listing endpoints" },
  list_dependencies: { icon: Package, working: "Listing dependencies" },
  find_imports: { icon: Network, working: "Finding imports" },
  architecture: { icon: Boxes, working: "Mapping the architecture" },
  related: { icon: Waypoints, working: "Following links" },
  impact: { icon: Radar, working: "Tracing dependants" },
};

export function describeStep(step: ToolStep): string {
  if (!step.done) return TOOLS[step.name]?.working ?? step.name;
  const input = step.input ?? {};
  const str = (key: string) => (typeof input[key] === "string" ? (input[key] as string) : "");
  switch (step.name) {
    case "repo_overview":
      return "Repository overview";
    case "search_code":
      return `“${str("query")}”`;
    case "read_file": {
      const name = str("path").split("/").pop();
      return input.start_line ? `${name}:${input.start_line}-${input.end_line ?? ""}` : `${name}`;
    }
    case "grep":
      return `/${str("pattern")}/`;
    case "find_symbol":
      return str("name");
    case "list_endpoints":
      return "Endpoints";
    case "list_dependencies":
      return "Dependencies";
    case "find_imports":
      return `imports of ${str("module_contains")}`;
    case "architecture":
      return typeof input.module === "number" ? `Module ${input.module}` : "Architecture";
    case "related":
      return `links of ${str("symbol")}`;
    case "impact":
      return `impact of ${str("symbol")}`;
    default:
      return step.name;
  }
}

interface Props {
  steps: ToolStep[];
  investigating: boolean;
  linkedSources: Source[] | null;
  onHoverStep: (sources: Source[] | null) => void;
  onOpenSource: (source: Source) => void;
}

/**
 * The agent's investigation as a trail of steps, appearing live as it works. A step lights up when the
 * claim under the cursor relies on code it found; hovering a step lights up the claims it supports.
 */
export function InvestigationTrail({ steps, investigating, linkedSources, onHoverStep, onOpenSource }: Props) {
  const [open, setOpen] = useState<number | null>(null);
  const files = new Set(steps.flatMap((s) => s.sources.map((x) => x.path)));
  const isLinked = (step: ToolStep) =>
    !!linkedSources && step.sources.some((s) => linkedSources.some((l) => sourcesOverlap(s, l)));
  const anyLinked = !!linkedSources && steps.some(isLinked);
  const selected = open !== null ? steps[open] : null;

  return (
    <div className="text-xs">
      <div className="mb-1.5 flex items-center gap-2 text-muted">
        {investigating ? (
          <>
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent opacity-60" />
              <span className="relative inline-flex size-2 rounded-full bg-accent" />
            </span>
            Investigating
          </>
        ) : (
          <>
            Investigated in {steps.length} {steps.length === 1 ? "step" : "steps"}
            {files.size > 0 && ` · looked at ${files.size} ${files.size === 1 ? "file" : "files"}`}
          </>
        )}
      </div>
      <ol className="flex flex-wrap items-center gap-y-1.5">
        {steps.map((step, i) => {
          const Icon = TOOLS[step.name]?.icon ?? Search;
          const linked = isLinked(step);
          return (
            <li key={i} className="trail-in flex items-center" style={{ animationDelay: `${Math.min(i, 3) * 30}ms` }}>
              {i > 0 && <ChevronRight className="mx-0.5 size-3 shrink-0 text-muted/70" aria-hidden />}
              <button
                type="button"
                onClick={() => setOpen(open === i ? null : i)}
                onMouseEnter={() => step.sources.length && onHoverStep(step.sources)}
                onMouseLeave={() => onHoverStep(null)}
                title={step.summary ?? describeStep(step)}
                className={`flex max-w-[15rem] items-center gap-1.5 rounded-md border px-2 py-1 transition-all ${
                  step.isError
                    ? "border-danger/40 text-danger"
                    : linked
                      ? "border-accent bg-accent-soft text-accent"
                      : open === i
                        ? "border-foreground/30 bg-code"
                        : "border-border bg-panel hover:border-accent/60"
                } ${anyLinked && !linked ? "opacity-45" : ""}`}
              >
                {step.done ? (
                  <Icon className="size-3.5 shrink-0" aria-hidden />
                ) : (
                  <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
                )}
                <span className={`truncate ${step.name === "read_file" || step.name === "grep" ? "font-mono text-[11px]" : ""}`}>
                  {describeStep(step)}
                </span>
                {step.done && step.sources.length > 0 && (
                  <span className="rounded bg-background px-1 text-[10px] tabular-nums text-muted">
                    {dedupeSources(step.sources).length}
                  </span>
                )}
              </button>
            </li>
          );
        })}
        {investigating && steps.length > 0 && steps.every((s) => s.done) && (
          <li className="flex items-center text-muted">
            <ChevronRight className="mx-0.5 size-3 text-muted/70" aria-hidden />
            <span className="animate-pulse px-1">thinking</span>
          </li>
        )}
      </ol>
      {selected && (
        <div className="trail-in mt-2 rounded-md border border-border bg-panel px-3 py-2">
          {(selected.summary || selected.ms !== undefined) && (
            <p className="text-muted">
              {selected.summary}
              {selected.ms !== undefined && (
                <span className="ml-2 tabular-nums text-muted/80">{formatMs(selected.ms)}</span>
              )}
            </p>
          )}
          {selected.sources.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {dedupeSources(selected.sources)
                .slice(0, 12)
                .map((s) => (
                  <button
                    key={`${s.path}:${s.start_line}-${s.end_line}`}
                    onClick={() => onOpenSource(s)}
                    title={s.path}
                    className="rounded border border-border bg-background px-1.5 font-mono text-[11px] text-accent hover:border-accent"
                  >
                    {shortSource(s)}
                  </button>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
