"use client";

import { useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/Markdown";
import { streamChat, type Repo, type Source } from "@/lib/api";

interface ToolStep {
  name: string;
  input?: Record<string, unknown>;
  summary?: string;
  sources: Source[];
  isError?: boolean;
  done: boolean;
}

interface Message {
  role: "user" | "assistant";
  text: string;
  steps: ToolStep[];
  notices: string[];
  error?: string;
  usage?: Record<string, number>;
  pending?: boolean;
}

const SUGGESTIONS = [
  "Give me an overview of how this codebase is organised.",
  "List all the API endpoints and the handlers behind them.",
  "What are the main dependencies and what is each used for?",
  "How is authentication implemented?",
];

export function Chat({ repo, onOpenSource }: { repo: Repo; onOpenSource: (source: Source) => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  useEffect(() => () => abort.current?.abort(), []);

  const updateLast = (fn: (m: Message) => Message) =>
    setMessages((all) => [...all.slice(0, -1), fn(all[all.length - 1])]);

  const ask = async (question: string) => {
    if (!question.trim() || running) return;
    setInput("");
    setRunning(true);
    setMessages((all) => [
      ...all,
      { role: "user", text: question, steps: [], notices: [] },
      { role: "assistant", text: "", steps: [], notices: [], pending: true },
    ]);
    abort.current = new AbortController();
    try {
      for await (const event of streamChat(repo.id, question, conversationId, abort.current.signal)) {
        switch (event.type) {
          case "conversation":
            setConversationId(event.id);
            break;
          case "text":
            updateLast((m) => ({ ...m, text: m.text + event.delta }));
            break;
          case "tool_start":
            updateLast((m) => ({ ...m, steps: [...m.steps, { name: event.name, sources: [], done: false }] }));
            break;
          case "tool_result":
            updateLast((m) => {
              const steps = [...m.steps];
              const index = steps.findIndex((s) => s.name === event.name && !s.done);
              const step = {
                name: event.name,
                input: event.input,
                summary: event.summary,
                sources: event.sources,
                isError: event.is_error,
                done: true,
              };
              if (index === -1) steps.push(step);
              else steps[index] = step;
              return { ...m, steps };
            });
            break;
          case "notice":
            updateLast((m) => ({ ...m, notices: [...m.notices, event.message] }));
            break;
          case "error":
            updateLast((m) => ({ ...m, error: event.message }));
            break;
          case "done":
            updateLast((m) => ({ ...m, usage: event.usage }));
            break;
        }
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        updateLast((m) => ({ ...m, error: error instanceof Error ? error.message : String(error) }));
      }
    } finally {
      updateLast((m) => ({ ...m, pending: false }));
      setRunning(false);
    }
  };

  const ready = repo.status === "ready";

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {messages.length === 0 && (
          <div className="mx-auto max-w-xl pt-10">
            <p className="text-sm text-muted">
              {ready
                ? `Ask anything about ${repo.name}. Answers cite the exact files and lines they rely on.`
                : "This repository is still being indexed. You can ask questions once it is ready."}
            </p>
            {ready && (
              <div className="mt-4 grid gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    className="rounded-lg border border-border px-3 py-2 text-left text-sm hover:border-accent hover:bg-accent-soft"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="self-end rounded-2xl bg-accent-soft px-4 py-2 text-[14.5px] whitespace-pre-wrap">
                {m.text}
              </div>
            ) : (
              <AssistantMessage key={i} message={m} onOpenSource={onOpenSource} />
            ),
          )}
        </div>
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
        className="border-t border-border p-3"
      >
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                ask(input);
              }
            }}
            rows={Math.min(6, Math.max(1, input.split("\n").length))}
            placeholder={ready ? "Ask about the code…  (Enter to send, Shift+Enter for a new line)" : "Indexing…"}
            disabled={!ready}
            className="min-h-[42px] flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2.5 text-sm outline-none focus:border-accent disabled:opacity-60"
          />
          {running ? (
            <button
              type="button"
              onClick={() => abort.current?.abort()}
              className="rounded-lg border border-border px-3 py-2.5 text-sm"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!ready || !input.trim()}
              className="rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50 dark:text-black"
            >
              Ask
            </button>
          )}
        </div>
        {conversationId && !running && (
          <div className="mx-auto mt-1.5 max-w-3xl text-right">
            <button
              type="button"
              className="text-xs text-muted hover:text-foreground"
              onClick={() => {
                setMessages([]);
                setConversationId(null);
              }}
            >
              New conversation
            </button>
          </div>
        )}
      </form>
    </>
  );
}

function AssistantMessage({ message, onOpenSource }: { message: Message; onOpenSource: (s: Source) => void }) {
  const [open, setOpen] = useState(false);
  const steps = message.steps;
  return (
    <div className="flex flex-col gap-2">
      {steps.length > 0 && (
        <div className="rounded-lg border border-border bg-panel text-xs">
          <button
            onClick={() => setOpen(!open)}
            className="flex w-full items-center justify-between px-3 py-2 text-muted hover:text-foreground"
          >
            <span>
              {message.pending && !message.text ? "Investigating… " : ""}
              {steps.length} tool {steps.length === 1 ? "call" : "calls"}:{" "}
              {describe(steps[steps.length - 1])}
            </span>
            <span>{open ? "Hide" : "Show"}</span>
          </button>
          {open && (
            <ol className="space-y-1.5 border-t border-border px-3 py-2">
              {steps.map((step, i) => (
                <li key={i}>
                  <span className={step.isError ? "text-danger" : ""}>{describe(step)}</span>
                  {step.summary && <span className="text-muted"> — {step.summary}</span>}
                  {step.sources.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {dedupe(step.sources)
                        .slice(0, 8)
                        .map((s, j) => (
                          <button
                            key={j}
                            onClick={() => onOpenSource(s)}
                            className="rounded border border-border bg-background px-1.5 font-mono text-[11px] text-accent hover:border-accent"
                          >
                            {s.path}:{s.start_line}
                            {s.end_line !== s.start_line ? `-${s.end_line}` : ""}
                          </button>
                        ))}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
      {message.text && <Markdown text={message.text} onOpenSource={onOpenSource} />}
      {message.pending && !message.text && steps.length === 0 && (
        <p className="animate-pulse text-sm text-muted">Thinking…</p>
      )}
      {message.notices.map((n, i) => (
        <p key={i} className="text-xs text-muted">
          {n}
        </p>
      ))}
      {message.error && (
        <p className="rounded-md border border-danger/40 px-3 py-2 text-sm text-danger">{message.error}</p>
      )}
      {message.usage && !message.pending && (
        <p className="text-[11px] text-muted">
          {(message.usage.input_tokens ?? 0) +
            (message.usage.cache_read_input_tokens ?? 0) +
            (message.usage.cache_creation_input_tokens ?? 0)}{" "}
          input tokens ({message.usage.cache_read_input_tokens ?? 0} from cache) · {message.usage.output_tokens ?? 0}{" "}
          output tokens
        </p>
      )}
    </div>
  );
}

function dedupe(sources: Source[]): Source[] {
  const seen = new Set<string>();
  return sources.filter((s) => {
    const key = `${s.path}:${s.start_line}-${s.end_line}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function describe(step: ToolStep): string {
  const input = step.input ?? {};
  const str = (key: string) => (typeof input[key] === "string" ? (input[key] as string) : "");
  switch (step.name) {
    case "repo_overview":
      return "Read the repository overview";
    case "search_code":
      return `Searched “${str("query")}”`;
    case "read_file": {
      const range = input.start_line ? `:${input.start_line}-${input.end_line ?? ""}` : "";
      return `Read ${str("path")}${range}`;
    }
    case "grep":
      return `Grepped /${str("pattern")}/`;
    case "find_symbol":
      return `Looked up ${str("name")}`;
    case "list_endpoints":
      return "Listed endpoints";
    case "list_dependencies":
      return "Listed dependencies";
    case "find_imports":
      return `Found imports of ${str("module_contains")}`;
    default:
      return step.name;
  }
}
