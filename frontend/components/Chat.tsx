"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AnswerView, type ActivationVia } from "@/components/AnswerView";
import { InvestigationTrail, type ToolStep } from "@/components/InvestigationTrail";
import { streamChat, type Evidence, type Repo, type Source } from "@/lib/api";

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

type Activate = (msg: number, key: string, sources: Source[], label: string, via: ActivationVia, index?: number) => void;

interface Props {
  repo: Repo;
  onEvidence: (evidence: Evidence | null) => void;
}

export function Chat({ repo, onEvidence }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [active, setActive] = useState<{ msg: number; key: string } | null>(null);
  // Cross-highlighting between the trail and the answer: sources under the cursor, per message.
  const [hovered, setHovered] = useState<{ msg: number; from: "claim" | "step"; sources: Source[] } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const activeRef = useRef<{ msg: number; key: string } | null>(null);
  const lastPointer = useRef(0);

  useEffect(() => {
    if (running) bottom.current?.scrollIntoView({ block: "end" });
  }, [messages, running]);

  const openSource = useCallback((source: Source) => onEvidence({ sources: [source], index: 0 }), [onEvidence]);

  const activate: Activate = useCallback(
    (msg, key, sources, label, via, index = 0) => {
      const now = Date.now();
      // Scrolling follows the reader, but never fights a hover or click that just happened.
      if (via === "scroll" && now - lastPointer.current < 1500) return;
      if (via !== "scroll") lastPointer.current = now;
      const current = activeRef.current;
      if (current?.msg === msg && current.key === key && via !== "click") return;
      activeRef.current = { msg, key };
      setActive({ msg, key });
      if (sources.length) onEvidence({ sources, index, label });
    },
    [onEvidence],
  );

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
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
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
              <AssistantMessage
                key={i}
                index={i}
                message={m}
                activeKey={active?.msg === i ? active.key : null}
                hovered={hovered?.msg === i ? hovered : null}
                scrollRoot={scroller}
                onActivate={activate}
                onHover={(from, sources) => setHovered(sources ? { msg: i, from, sources } : null)}
                onOpenSource={openSource}
              />
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
            placeholder={ready ? "Ask about the code…" : "Indexing…"}
            title="Enter to send, Shift+Enter for a new line"
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
                setActive(null);
                activeRef.current = null;
                onEvidence(null);
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

interface AssistantProps {
  index: number;
  message: Message;
  activeKey: string | null;
  hovered: { from: "claim" | "step"; sources: Source[] } | null;
  scrollRoot: React.RefObject<HTMLDivElement | null>;
  onActivate: Activate;
  onHover: (from: "claim" | "step", sources: Source[] | null) => void;
  onOpenSource: (source: Source) => void;
}

function AssistantMessage({ index, message, activeKey, hovered, scrollRoot, onActivate, onHover, onOpenSource }: AssistantProps) {
  const activateHere = useCallback(
    (key: string, sources: Source[], label: string, via: ActivationVia, i?: number) =>
      onActivate(index, key, sources, label, via, i),
    [index, onActivate],
  );
  const onHoverClaim = useCallback((sources: Source[] | null) => onHover("claim", sources), [onHover]);
  const steps = message.steps;
  return (
    <div className="flex flex-col gap-3">
      {steps.length > 0 && (
        <InvestigationTrail
          steps={steps}
          investigating={!!message.pending && !message.text}
          linkedSources={hovered?.from === "claim" ? hovered.sources : null}
          onHoverStep={(sources) => onHover("step", sources)}
          onOpenSource={onOpenSource}
        />
      )}
      {message.text && (
        <AnswerView
          text={message.text}
          pending={!!message.pending}
          activeKey={activeKey}
          linkedSources={hovered?.from === "step" ? hovered.sources : null}
          scrollRoot={scrollRoot}
          onActivate={activateHere}
          onHoverClaim={onHoverClaim}
          onOpenSource={onOpenSource}
        />
      )}
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
        <p className="pl-8 text-[11px] text-muted">
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
