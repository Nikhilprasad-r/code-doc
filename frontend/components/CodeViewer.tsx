"use client";

import { useEffect, useRef, useState } from "react";

import { api, type FileContent, type Source } from "@/lib/api";

interface Props {
  repoId: number;
  source: Source;
  onClose: () => void;
}

export function CodeViewer({ repoId, source, onClose }: Props) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const firstHighlighted = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .file(repoId, source.path)
      .then((f) => {
        if (cancelled) return;
        setFile(f);
        setError(null);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [repoId, source.path]);

  useEffect(() => {
    firstHighlighted.current?.scrollIntoView({ block: "center" });
  }, [file, source.start_line, source.end_line]);

  const lines = file && file.path === source.path ? file.content.split("\n") : [];
  const width = String(lines.length).length;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0">
          <p className="truncate font-mono text-xs">{source.path}</p>
          <p className="text-[11px] text-muted">
            lines {source.start_line}
            {source.end_line !== source.start_line ? `–${source.end_line}` : ""}
            {file ? ` of ${file.line_count}` : ""}
          </p>
        </div>
        <button onClick={onClose} className="rounded px-2 py-1 text-xs text-muted hover:bg-code hover:text-foreground">
          Close
        </button>
      </div>
      {error && <p className="p-3 text-sm text-danger">{error}</p>}
      <div className="min-h-0 flex-1 overflow-auto bg-panel py-2 font-mono text-[12.5px] leading-[1.6]">
        {lines.map((line, i) => {
          const number = i + 1;
          const highlighted = number >= source.start_line && number <= source.end_line;
          return (
            <div
              key={number}
              ref={number === source.start_line ? firstHighlighted : undefined}
              className={`flex whitespace-pre ${highlighted ? "bg-highlight" : ""}`}
            >
              <span className="sticky left-0 shrink-0 select-none bg-inherit pr-3 pl-3 text-right text-muted" style={{ minWidth: `${width + 3}ch` }}>
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
