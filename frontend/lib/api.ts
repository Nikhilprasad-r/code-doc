export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type RepoStatus = "queued" | "indexing" | "ready" | "failed";

export interface RepoStats {
  files?: number;
  lines?: number;
  chunks?: number;
  symbols?: number;
  endpoints?: number;
  dependencies?: number;
  languages?: Record<string, number>;
  embed_model?: string;
  seconds?: number;
  /** Code graph from graphify, or the reason it could not be built. */
  graph?: { nodes?: number; edges?: number; communities?: number; error?: string } | null;
}

export interface Repo {
  id: number;
  name: string;
  source: string;
  ref: string | null;
  commit_sha: string | null;
  status: RepoStatus;
  progress: { phase?: string; done?: number; total?: number };
  stats: RepoStats;
  error: string | null;
}

export interface Source {
  path: string;
  start_line: number;
  end_line: number;
}

/** What the evidence panel shows: the citations behind one claim, which one is in focus, and a label. */
export interface Evidence {
  sources: Source[];
  index: number;
  label?: string;
}

const fileCache = new Map<string, Promise<FileContent>>();

export interface FileContent {
  path: string;
  language: string | null;
  content: string;
  line_count: number;
}

export interface EndpointRow {
  framework: string;
  method: string;
  route: string;
  handler: string | null;
  path: string;
  line: number;
}

export interface DependencyRow {
  ecosystem: string;
  name: string;
  version_spec: string | null;
  scope: string | null;
  manifest_path: string;
}

export interface Health {
  ok: boolean;
  offline_embeddings: boolean;
  agent_model: string;
  read_only: boolean;
}

export type ChatEvent =
  | { type: "conversation"; id: string }
  | { type: "text"; delta: string }
  | { type: "tool_start"; name: string }
  | {
      type: "tool_result";
      id: string;
      name: string;
      input: Record<string, unknown>;
      summary: string;
      sources: Source[];
      is_error: boolean;
    }
  | { type: "notice"; message: string }
  | { type: "error"; message: string }
  | { type: "done"; usage: Record<string, number> };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // keep statusText
    }
    throw new Error(detail);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export const api = {
  health: () => request<Health>("/health"),
  listRepos: () => request<Repo[]>("/repos"),
  addRepo: (source: string, ref?: string) =>
    request<Repo>("/repos", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source, ref: ref || null }),
    }),
  uploadRepo: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Repo>("/repos/upload", { method: "POST", body: form });
  },
  reindex: (id: number) => request<Repo>(`/repos/${id}/reindex`, { method: "POST" }),
  deleteRepo: (id: number) => request<void>(`/repos/${id}`, { method: "DELETE" }),
  /** Cached per repository and commit: the evidence panel re-opens the same files as you move between claims. */
  file: (id: number, path: string, commit?: string | null) => {
    const key = `${id}@${commit ?? ""}:${path}`;
    let pending = fileCache.get(key);
    if (!pending) {
      pending = request<FileContent>(`/repos/${id}/file?path=${encodeURIComponent(path)}`);
      pending.catch(() => fileCache.delete(key));
      fileCache.set(key, pending);
    }
    return pending;
  },
  endpoints: (id: number) => request<EndpointRow[]>(`/repos/${id}/endpoints`),
  dependencies: (id: number) => request<DependencyRow[]>(`/repos/${id}/dependencies`),
};

/** POST /chat and yield server-sent events as they arrive. */
export async function* streamChat(
  repoId: number,
  message: string,
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_id: repoId, message, conversation_id: conversationId }),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // keep statusText
    }
    yield { type: "error", message: String(detail) };
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (data) yield JSON.parse(data) as ChatEvent;
      boundary = buffer.indexOf("\n\n");
    }
  }
}
