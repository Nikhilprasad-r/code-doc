"use client";

import { useRef, useState } from "react";

import { api, type Repo } from "@/lib/api";

interface Props {
  repos: Repo[];
  selectedId: number | null;
  readOnly: boolean;
  onSelect: (id: number) => void;
  onChanged: () => void;
}

export function RepoSidebar({ repos, selectedId, readOnly, onSelect, onChanged }: Props) {
  const [source, setSource] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const run = async (action: () => Promise<Repo | void>) => {
    setBusy(true);
    setError(null);
    try {
      const repo = await action();
      onChanged();
      if (repo) onSelect(repo.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!source.trim()) return;
    run(async () => {
      const repo = await api.addRepo(source.trim(), ref.trim() || undefined);
      setSource("");
      setRef("");
      return repo;
    });
  };

  return (
    <nav className="flex min-h-0 flex-col bg-panel">
      {!readOnly && (
        <form onSubmit={submit} className="space-y-2 border-b border-border p-3">
          <label className="block text-xs font-medium text-muted" htmlFor="repo-source">
            Add repository
          </label>
          <input
            id="repo-source"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="https://github.com/owner/repo"
            className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm outline-none focus:border-accent"
          />
          <div className="flex gap-2">
            <input
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder="branch (optional)"
              className="min-w-0 flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm outline-none focus:border-accent"
            />
            <button
              type="submit"
              disabled={busy || !source.trim()}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:text-black"
            >
              Index
            </button>
          </div>
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={busy}
            className="text-xs text-accent hover:underline disabled:opacity-50"
          >
            or upload a .zip
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) run(() => api.uploadRepo(file));
            }}
          />
          {error && <p className="text-xs text-danger">{error}</p>}
        </form>
      )}

      <ul className="min-h-0 flex-1 overflow-y-auto p-2">
        {repos.map((repo) => (
          <li key={repo.id}>
            <button
              onClick={() => onSelect(repo.id)}
              className={`group w-full rounded-md px-2.5 py-2 text-left ${
                repo.id === selectedId ? "bg-accent-soft" : "hover:bg-code"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{repo.name}</span>
                <StatusDot status={repo.status} />
              </div>
              <RepoLine repo={repo} />
            </button>
          </li>
        ))}
        {repos.length === 0 && <li className="px-2.5 py-2 text-xs text-muted">No repositories yet.</li>}
      </ul>

      {!readOnly && repos.find((r) => r.id === selectedId) && (
        <div className="flex gap-3 border-t border-border p-3 text-xs">
          <button className="text-muted hover:text-foreground" onClick={() => run(() => api.reindex(selectedId!))}>
            Re-index
          </button>
          <button
            className="text-muted hover:text-danger"
            onClick={() => {
              if (confirm("Delete this repository and its index?")) run(() => api.deleteRepo(selectedId!));
            }}
          >
            Delete
          </button>
        </div>
      )}
    </nav>
  );
}

function StatusDot({ status }: { status: Repo["status"] }) {
  const color = {
    ready: "bg-ok",
    failed: "bg-danger",
    indexing: "bg-accent animate-pulse",
    queued: "bg-muted animate-pulse",
  }[status];
  return <span title={status} className={`size-2 shrink-0 rounded-full ${color}`} />;
}

function RepoLine({ repo }: { repo: Repo }) {
  if (repo.status === "failed") {
    return <p className="mt-0.5 line-clamp-2 text-xs text-danger">{repo.error ?? "Indexing failed"}</p>;
  }
  if (repo.status !== "ready") {
    const { phase, done, total } = repo.progress ?? {};
    const detail = phase === "embedding" && total ? ` ${done ?? 0}/${total} chunks` : "";
    return (
      <p className="mt-0.5 text-xs text-muted">
        {phase ?? repo.status}
        {detail}…
      </p>
    );
  }
  const s = repo.stats;
  return (
    <p className="mt-0.5 text-xs text-muted">
      {s.files} files · {s.endpoints ?? 0} endpoints · {s.dependencies ?? 0} deps
    </p>
  );
}
