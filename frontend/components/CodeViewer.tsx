"use client";

import { Crosshair, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, type Evidence, type FileContent } from "@/lib/api";
import { shortSource } from "@/lib/citations";

interface Props {
  repoId: number;
  commit: string | null;
  evidence: Evidence;
  onSelect: (index: number) => void;
  onClose: () => void;
}

/**
 * The evidence panel: the code behind the claim in focus. Every citation of the claim is highlighted
 * in its file; the focused one is outlined and scrolled into view.
 */
export function CodeViewer({ repoId, commit, evidence, onSelect, onClose }: Props) {
  const source = evidence.sources[Math.min(evidence.index, evidence.sources.length - 1)];
  // Keyed by the cited path: a shortened citation (`Visit.java`) comes back with the file's full path.
  const [loaded, setLoaded] = useState<{ cited: string; file: FileContent } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const focusLine = useRef<HTMLDivElement>(null);
  const firstLoad = useRef(true);

  useEffect(() => {
    let cancelled = false;
    api
      .file(repoId, source.path, commit)
      .then((f) => {
        if (cancelled) return;
        setLoaded({ cited: source.path, file: f });
        setError(null);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [repoId, commit, source.path]);

  const file = loaded && loaded.cited === source.path ? loaded.file : null;

  useEffect(() => {
    if (!file) return;
    focusLine.current?.scrollIntoView({ block: "center", behavior: firstLoad.current ? "auto" : "smooth" });
    firstLoad.current = false;
  }, [file, source.path, source.start_line, source.end_line]);

  const lines = file ? file.content.split("\n") : [];
  const width = String(lines.length).length;
  const sameFile = evidence.sources.filter((s) => s.path === source.path);
  const inRange = (n: number, s: { start_line: number; end_line: number }) => n >= s.start_line && n <= s.end_line;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="space-y-2 border-b border-border px-3 py-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            {evidence.label ? (
              <p className="flex items-start gap-1.5 text-[13px] leading-snug">
                <Crosshair className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
                <span className="line-clamp-2">{evidence.label}</span>
              </p>
            ) : (
              <p className="text-xs text-muted">Source</p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close the evidence panel"
            className="rounded p-1 text-muted hover:bg-code hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
        {evidence.sources.length > 1 && (
          <div className="flex flex-wrap gap-1">
            {evidence.sources.map((s, i) => (
              <button
                key={`${s.path}:${s.start_line}-${s.end_line}:${i}`}
                onClick={() => onSelect(i)}
                title={s.path}
                className={`rounded border px-1.5 py-px font-mono text-[11px] transition-colors ${
                  i === evidence.index
                    ? "border-accent bg-accent text-white dark:text-black"
                    : "border-border bg-background text-accent hover:border-accent"
                }`}
              >
                {shortSource(s)}
              </button>
            ))}
          </div>
        )}
        <p className="truncate font-mono text-[11px] text-muted" title={file?.path ?? source.path}>
          {file?.path ?? source.path} · lines {source.start_line}
          {source.end_line !== source.start_line ? `–${source.end_line}` : ""}
          {file ? ` of ${file.line_count}` : ""}
          {file && file.path !== source.path ? ` (cited as ${source.path})` : ""}
        </p>
      </div>
      {error && <p className="p-3 text-sm text-danger">{error}</p>}
      <div className="min-h-0 flex-1 overflow-auto bg-panel py-2 font-mono text-[12.5px] leading-[1.6]">
        {lines.map((line, i) => {
          const number = i + 1;
          const focused = inRange(number, source);
          const cited = !focused && sameFile.some((s) => inRange(number, s));
          return (
            <div
              key={number}
              ref={number === source.start_line ? focusLine : undefined}
              className={`flex whitespace-pre transition-colors duration-300 ${
                focused ? "bg-highlight" : cited ? "bg-highlight/40" : ""
              }`}
            >
              <span
                className={`sticky left-0 shrink-0 border-l-2 pr-3 pl-2.5 text-right select-none ${
                  focused ? "border-accent bg-inherit text-foreground" : `border-transparent text-muted ${cited ? "bg-inherit" : "bg-panel"}`
                }`}
                style={{ minWidth: `${width + 3}ch` }}
              >
                {number}
              </span>
              <span className="pr-4">{line || " "}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
