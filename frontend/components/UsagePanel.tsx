"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { Table } from "@/components/Table";
import { api, type AnswerRun, type AnswerStats, type AnswerStatus } from "@/lib/api";
import { formatCost, formatCount, formatMs, modelLabel } from "@/lib/format";

const DAYS = 7;

const STATUS_LABEL: Record<AnswerStatus, string> = {
  ok: "ok",
  error: "error",
  refused: "refused",
  cut_off: "cut off",
  cancelled: "cancelled",
};

/** The answer log for one repository: cost, latency, tool health and recent answers (GET /stats, /answers). */
export function UsagePanel({ repoId }: { repoId: number }) {
  const [stats, setStats] = useState<AnswerStats | null>(null);
  const [runs, setRuns] = useState<AnswerRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.stats(repoId, DAYS), api.answers(repoId, 20)])
      .then(([s, r]) => {
        if (cancelled) return;
        setStats(s);
        setRuns(r);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [repoId, version]);

  if (error) return <p className="text-danger">Could not load usage: {error}</p>;
  if (!stats || !runs) return <p className="text-muted">Loading usage…</p>;

  const notOk = stats.answers - stats.ok;
  const tiles: [string, string, string?][] = [
    ["Answers", formatCount(stats.answers), notOk ? `${notOk} not ok` : undefined],
    ["Spend", formatCost(stats.cost_usd), stats.answers ? `${formatCost(stats.cost_usd / stats.answers)} per answer` : undefined],
    ["Latency p50 / p95", `${formatMs(stats.latency_p50_ms)} / ${formatMs(stats.latency_p95_ms)}`],
    ["First words p50", formatMs(stats.ttft_p50_ms)],
    ["Error rate", stats.answers ? `${Math.round((notOk / stats.answers) * 100)}%` : "–"],
    ["Tool calls per answer", stats.avg_tool_calls === null ? "–" : Number(stats.avg_tool_calls).toFixed(1)],
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted">Last {DAYS} days, from the chat and MCP. Successful answers only in latency.</p>
        <button
          onClick={() => setVersion((v) => v + 1)}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted hover:text-foreground"
        >
          <RefreshCw className="size-3" aria-hidden /> Refresh
        </button>
      </div>

      {stats.answers === 0 ? (
        <p className="text-muted">No answers yet. Ask a question and its cost, latency and tool calls show up here.</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
            {tiles.map(([label, value, note]) => (
              <div key={label}>
                <dt className="text-xs text-muted">{label}</dt>
                <dd className="font-medium tabular-nums">{value}</dd>
                {note && <dd className="text-[11px] text-muted">{note}</dd>}
              </div>
            ))}
          </dl>

          <section>
            <h3 className="mb-2 text-xs text-muted">By model</h3>
            <Table
              head={["Model", "Answers", "Spend", "Median latency"]}
              rows={stats.by_model.map((m) => [
                modelLabel(m.model),
                <span key="n" className="tabular-nums">{m.answers}</span>,
                <span key="c" className="tabular-nums">{formatCost(m.cost_usd)}</span>,
                <span key="l" className="tabular-nums">{formatMs(m.latency_p50_ms)}</span>,
              ])}
              empty="No answers."
            />
          </section>

          <section>
            <h3 className="mb-2 text-xs text-muted">Tools</h3>
            <Table
              head={["Tool", "Calls", "Errors", "p50", "p95"]}
              rows={stats.tools.map((t) => [
                <span key="t" className="font-mono text-xs">{t.name}</span>,
                <span key="n" className="tabular-nums">{t.calls}</span>,
                <span key="e" className={`tabular-nums ${t.errors ? "text-danger" : ""}`}>
                  {t.errors ? `${t.errors} (${Math.round(t.error_rate * 100)}%)` : "0"}
                </span>,
                <span key="p50" className="tabular-nums">{formatMs(t.p50_ms)}</span>,
                <span key="p95" className="tabular-nums">{formatMs(t.p95_ms)}</span>,
              ])}
              empty="No tool calls yet."
            />
          </section>

          <section>
            <h3 className="mb-2 text-xs text-muted">Recent answers</h3>
            <Table
              head={["Question", "Model", "Time", "Cost", "Status"]}
              rows={runs.map((r) => [
                <span key="q" className="line-clamp-2" title={r.question ?? undefined}>
                  {r.source === "mcp" && <span className="mr-1 rounded bg-code px-1 text-[10px] text-muted">MCP</span>}
                  {r.question ?? <span className="text-muted">(not recorded)</span>}
                </span>,
                <span key="m" className="whitespace-nowrap">{modelLabel(r.model)}</span>,
                <span key="t" className="whitespace-nowrap tabular-nums" title={`${r.tool_calls} tool calls`}>
                  {formatMs(r.latency_ms)}
                </span>,
                <span key="c" className="tabular-nums">{formatCost(r.cost_usd)}</span>,
                <span
                  key="s"
                  className={`whitespace-nowrap text-xs ${r.status === "ok" ? "text-muted" : "text-danger"}`}
                  title={r.error ?? undefined}
                >
                  {STATUS_LABEL[r.status] ?? r.status}
                </span>,
              ])}
              empty="No answers yet."
            />
          </section>
        </>
      )}
    </div>
  );
}
