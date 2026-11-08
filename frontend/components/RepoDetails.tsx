"use client";

import { useEffect, useState } from "react";

import { Table } from "@/components/Table";
import { UsagePanel } from "@/components/UsagePanel";
import { api, type DependencyRow, type EndpointRow, type Repo, type Source } from "@/lib/api";

type Tab = "overview" | "endpoints" | "dependencies" | "graph" | "usage";

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
  const hasGraph = repo.status === "ready" && !!repo.stats.graph && !repo.stats.graph.error;
  const tabs: Tab[] = ["overview", "endpoints", "dependencies", ...(hasGraph ? ["graph" as const] : []), "usage"];
  const shown = tab === "graph" && !hasGraph ? "overview" : tab;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex gap-1 border-b border-border px-2 pt-2">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-t-md px-3 py-1.5 text-xs capitalize ${
              shown === t ? "border border-b-0 border-border bg-background font-medium" : "text-muted hover:text-foreground"
            }`}
          >
            {t}
            {t === "endpoints" && endpoints ? ` (${routes.length})` : ""}
            {t === "dependencies" && dependencies ? ` (${dependencies.length})` : ""}
          </button>
        ))}
      </div>

      {shown === "graph" ? (
        <GraphView repo={repo} />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto p-4 text-sm">
          {shown === "overview" && <Overview repo={repo} />}
          {shown === "usage" && <UsagePanel repoId={repo.id} />}

          {shown === "endpoints" && (
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

          {shown === "dependencies" && (
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
      )}
    </div>
  );
}

/** graphify's own interactive page, sandboxed: its scripts get no access to this app or the API. */
function GraphView({ repo }: { repo: Repo }) {
  const src = api.graphUrl(repo.id, repo.commit_sha);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-3 px-4 py-2 text-xs text-muted">
        <span>graphify&apos;s view of every file and symbol it found. Click a node to see its links.</span>
        <a href={src} target="_blank" rel="noopener noreferrer" className="shrink-0 text-accent hover:underline">
          Open full screen
        </a>
      </div>
      <iframe
        key={src}
        src={src}
        title={`Code graph of ${repo.name}`}
        sandbox="allow-scripts"
        className="min-h-0 w-full flex-1 border-t border-border"
      />
    </div>
  );
}

function timingsSummary(timings: Repo["stats"]["timings"]): string | undefined {
  if (!timings || Object.keys(timings).length === 0) return undefined;
  return Object.entries(timings)
    .map(([phase, seconds]) => `${phase} ${seconds}s`)
    .join(" · ");
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
    ["Index phases", timingsSummary(s.timings)],
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
