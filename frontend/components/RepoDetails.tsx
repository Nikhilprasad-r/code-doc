"use client";

import { useEffect, useState } from "react";

import { api, type DependencyRow, type EndpointRow, type Repo, type Source } from "@/lib/api";

type Tab = "overview" | "endpoints" | "dependencies";

export function RepoDetails({ repo, onOpenSource }: { repo: Repo; onOpenSource: (s: Source) => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [endpoints, setEndpoints] = useState<EndpointRow[] | null>(null);
  const [dependencies, setDependencies] = useState<DependencyRow[] | null>(null);

  useEffect(() => {
    if (repo.status !== "ready") return;
    let cancelled = false;
    Promise.all([api.endpoints(repo.id), api.dependencies(repo.id)])
      .then(([e, d]) => {
        if (cancelled) return;
        setEndpoints(e);
        setDependencies(d);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [repo.id, repo.status, repo.commit_sha]);

  const routes = endpoints?.filter((e) => e.method !== "MOUNT") ?? [];
  const mounts = endpoints?.filter((e) => e.method === "MOUNT") ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex gap-1 border-b border-border px-2 pt-2">
        {(["overview", "endpoints", "dependencies"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-t-md px-3 py-1.5 text-xs capitalize ${
              tab === t ? "border border-b-0 border-border bg-background font-medium" : "text-muted hover:text-foreground"
            }`}
          >
            {t}
            {t === "endpoints" && endpoints ? ` (${routes.length})` : ""}
            {t === "dependencies" && dependencies ? ` (${dependencies.length})` : ""}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4 text-sm">
        {tab === "overview" && <Overview repo={repo} />}

        {tab === "endpoints" && (
          <>
            <p className="mb-3 text-xs text-muted">
              Found by static analysis. Routes are relative to their router; mounts show the prefix added where a
              router is included.
            </p>
            <Table
              head={["Method", "Route", "Handler", "Location"]}
              rows={[...routes, ...mounts].map((e) => [
                <span key="m" className="font-mono text-xs font-medium">
                  {e.method}
                </span>,
                <span key="r" className="font-mono text-xs">
                  {e.route || "/"}
                </span>,
                <span key="h" className="font-mono text-xs">
                  {e.handler ?? "?"}
                </span>,
                <SourceLink key="l" path={e.path} line={e.line} onOpen={onOpenSource} />,
              ])}
              empty="No endpoints detected."
            />
          </>
        )}

        {tab === "dependencies" && (
          <Table
            head={["Package", "Version", "Scope", "Manifest"]}
            rows={(dependencies ?? []).map((d) => [
              <span key="n" className="font-mono text-xs">
                {d.name}
              </span>,
              <span key="v" className="font-mono text-xs text-muted">
                {d.version_spec ?? ""}
              </span>,
              <span key="s" className="text-xs text-muted">
                {d.scope ?? ""}
              </span>,
              <SourceLink key="m" path={d.manifest_path} line={1} onOpen={onOpenSource} />,
            ])}
            empty="No dependency manifests found."
          />
        )}
      </div>
    </div>
  );
}

function graphSummary(graph: Repo["stats"]["graph"]): string | undefined {
  if (!graph) return undefined;
  if (graph.error) return "Not available";
  return `${graph.nodes} nodes, ${graph.edges} links, ${graph.communities} modules`;
}

function Overview({ repo }: { repo: Repo }) {
  const s = repo.stats;
  if (repo.status !== "ready") {
    return <p className="text-muted">Statistics appear once indexing finishes.</p>;
  }
  const facts: [string, string | number | undefined][] = [
    ["Files", s.files],
    ["Lines", s.lines?.toLocaleString()],
    ["Chunks", s.chunks],
    ["Symbols", s.symbols],
    ["Endpoints", s.endpoints],
    ["Dependencies", s.dependencies],
    ["Embedding model", s.embed_model],
    ["Indexed in", s.seconds !== undefined ? `${s.seconds}s` : undefined],
    ["Code graph", graphSummary(s.graph)],
  ];
  return (
    <div className="space-y-5">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="font-medium tabular-nums">{value ?? "–"}</dd>
          </div>
        ))}
      </dl>
      <div>
        <h3 className="mb-2 text-xs text-muted">Languages (files)</h3>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(s.languages ?? {}).map(([lang, count]) => (
            <span key={lang} className="rounded-full border border-border px-2 py-0.5 text-xs">
              {lang} <span className="text-muted">{count}</span>
            </span>
          ))}
        </div>
      </div>
      <p className="break-all text-xs text-muted">
        Source: {repo.source}
        {repo.commit_sha ? ` @ ${repo.commit_sha}` : ""}
      </p>
    </div>
  );
}

function SourceLink({ path, line, onOpen }: { path: string; line: number; onOpen: (s: Source) => void }) {
  const parts = path.split("/");
  const short = parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : path;
  return (
    <button
      onClick={() => onOpen({ path, start_line: line, end_line: line })}
      title={`${path}:${line}`}
      className="text-left font-mono text-xs whitespace-nowrap text-accent hover:underline"
    >
      {short}:{line}
    </button>
  );
}

function Table({ head, rows, empty }: { head: string[]; rows: React.ReactNode[][]; empty: string }) {
  if (rows.length === 0) return <p className="text-muted">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} className="border-b border-border pr-3 pb-1.5 text-left text-xs font-medium text-muted">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i} className="border-b border-border/60 align-top">
              {cells.map((cell, j) => (
                <td key={j} className="py-1.5 pr-3">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
