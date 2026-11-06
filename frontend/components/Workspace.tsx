"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Chat } from "@/components/Chat";
import { CodeViewer } from "@/components/CodeViewer";
import { RepoDetails } from "@/components/RepoDetails";
import { RepoSidebar } from "@/components/RepoSidebar";
import { api, type Health, type Repo, type Source } from "@/lib/api";

export function Workspace() {
  const params = useSearchParams();
  const router = useRouter();
  const repoId = Number(params.get("repo")) || null;

  const [repos, setRepos] = useState<Repo[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [viewer, setViewer] = useState<Source | null>(null);

  const refresh = useCallback(
    () =>
      api
        .listRepos()
        .then((list) => {
          setRepos(list);
          setConnectionError(null);
        })
        .catch((error) => setConnectionError(error instanceof Error ? error.message : String(error))),
    [],
  );

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
    api
      .listRepos()
      .then(setRepos)
      .catch((error) => setConnectionError(error instanceof Error ? error.message : String(error)));
  }, []);

  // Poll while any repository is still being indexed.
  const busy = repos.some((r) => r.status === "queued" || r.status === "indexing");
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  const selected = repos.find((r) => r.id === repoId) ?? null;

  const selectRepo = (id: number) => {
    setViewer(null);
    router.replace(`/?repo=${id}`);
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[15px] font-semibold tracking-tight">Code Docs Assistant</h1>
          {selected && (
            <span className="truncate text-sm text-muted">
              {selected.name}
              {selected.commit_sha && <span className="font-mono"> @ {selected.commit_sha.slice(0, 7)}</span>}
            </span>
          )}
        </div>
        {health && (
          <div className="hidden items-center gap-3 text-xs text-muted sm:flex">
            <span>
              Agent: <span className="font-mono">{health.agent_model}</span>
            </span>
            {health.offline_embeddings && (
              <span
                className="rounded-full border border-border px-2 py-0.5"
                title="VOYAGE_API_KEY is not set: vector search uses a hashing embedder"
              >
                offline embeddings
              </span>
            )}
          </div>
        )}
      </header>

      {connectionError && (
        <div className="border-b border-border bg-highlight px-4 py-2 text-sm">
          Cannot reach the API ({connectionError}). Is the backend running on{" "}
          <span className="font-mono">{process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}</span>?
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)_minmax(0,42%)]">
        <RepoSidebar
          repos={repos}
          selectedId={repoId}
          readOnly={health?.read_only ?? false}
          onSelect={selectRepo}
          onChanged={refresh}
        />
        <main className="flex min-h-0 flex-col border-border lg:border-x">
          {selected ? (
            <Chat key={selected.id} repo={selected} onOpenSource={setViewer} />
          ) : (
            <EmptyState hasRepos={repos.length > 0} />
          )}
        </main>
        <aside className="hidden min-h-0 flex-col lg:flex">
          {selected && viewer ? (
            <CodeViewer repoId={selected.id} source={viewer} onClose={() => setViewer(null)} />
          ) : selected ? (
            <RepoDetails repo={selected} onOpenSource={setViewer} />
          ) : null}
        </aside>
      </div>
    </div>
  );
}

function EmptyState({ hasRepos }: { hasRepos: boolean }) {
  return (
    <div className="m-auto max-w-sm p-8 text-center text-sm text-muted">
      {hasRepos
        ? "Pick a repository on the left to start asking questions."
        : "Add a GitHub URL or upload a zip on the left. Indexing a mid-sized repository takes about a minute."}
    </div>
  );
}
