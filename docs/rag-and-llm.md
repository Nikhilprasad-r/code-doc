# RAG and LLM approach

The starting point: code questions are rarely answered by one retrieved chunk. "How does adding a visit
work?" means finding the endpoint, reading the handler, following it to the entity and the database
mapping. "List all endpoints" needs every endpoint, not the top 8 matches. So retrieval has three
channels, and an agent picks between them for each question:

- **Search:** hybrid keyword + vector search over syntax-aware chunks, for behaviour and concepts.
- **Structured facts:** tables built at index time (symbols, imports, endpoints, dependencies),
  for questions that need complete or exact answers.
- **Code graph:** modules, a symbol's links, and what depends on it, for structure and impact
  questions.

**On this page:** [Chunking](#chunking) · [Embedding model](#embedding-model) ·
[Vector database and retrieval](#vector-database-and-retrieval) · [LLM](#llm) ·
[Orchestration framework](#orchestration-framework) · [Prompt and context management](#prompt-and-context-management) ·
[Guardrails](#guardrails) · [Quality](#quality) · [Observability](#observability)

## Chunking

| Files | Method |
|---|---|
| Python, JavaScript, TypeScript/TSX, Java, Go | **Syntax-aware**, after [cAST](https://arxiv.org/abs/2506.15655): pack neighbouring syntax-tree nodes up to a budget of 1,500 non-whitespace characters, split a node that is too big into its children, merge leftovers smaller than a quarter of the budget into a neighbour. Chunks cover the file exactly, with no overlap. |
| Markdown | One chunk per heading section, merged or split to the same budget. `#` lines inside fenced code blocks are not headings. |
| Everything else | Line windows up to the same budget |

Each chunk carries a header that goes into both the embedding and the keyword index: `File:`, `Scope:`
(the enclosing class or function) and `Defines:` (the symbols it contains). A method chunk deep inside
a class still says which class and file it belongs to. For Markdown, `Scope:` is the heading path, such as
`section Setup > Docker`, so every piece of a long section keeps its heading. The header has no line numbers on purpose:
code that only moved keeps the same cache key and is not embedded again.

Fixed-size windows cut functions in half and lose the enclosing scope. One chunk per function falls
apart on very small and very large functions. The eval's baseline design uses 40-line windows with
10 lines of overlap, so the difference can be measured.

## Embedding model

| | Choice | Why |
|---|---|---|
| Final | **Voyage `voyage-code-3`**, 1,024 dimensions, with separate `document` and `query` input types; **Voyage `rerank-2.5`** to rerank | Trained for code retrieval; one vendor for embedding and reranking; vectors cached by content hash, so re-indexing PetClinic unchanged re-embedded nothing (366 of 366 from cache) |
| Weighed | General-purpose text embeddings | Not trained on code; identifiers and code structure retrieve worse |
| Weighed | Open models hosted ourselves | No per-token cost or data leaving the network, but GPU hosting to run |
| Fallback | An offline hashing embedder (`hash-v1`) | Lets the stack and tests run without any key. It does not capture meaning, so it is for development only. |

## Vector database and retrieval

**Final choice: Postgres 17 with pgvector.**
- Vectors are in the `chunks` table, with an HNSW index on cosine distance.
- The same database holds the full-text index, symbols, endpoints, dependencies, the code graph and
  the answer log.
- That means one service to run, one transaction per index write, and SQL joins across indexes.

**Weighed:**
- Dedicated vector stores (Qdrant, Pinecone, turbopuffer) scale further, but add a second datastore
  that the keyword index and the tables can't join with.
- Elasticsearch/OpenSearch does BM25 and vectors in one engine, but is heavier to run for one
  repository at a time.

**Hybrid search** (`search_code`):
- Keyword search (Postgres full text over code-aware terms: `getUserById` is indexed as
  `getuserbyid get user by id`) and vector search each return 50 candidates.
- Reciprocal Rank Fusion (k=60) merges the two lists.
- Voyage `rerank-2.5` reorders the top candidates.
- The query's embedding is cached in memory (the last 2,048 queries, about 8 MB), so a repeated
  search skips the Voyage call. It pays off where the search text is the user's question: single-shot
  RAG, the same question asked again, and evaluation repeats. The agent rewords its searches, so
  inside one answer it rarely hits: none of the 6 searches in the answers recorded so far repeated.
  Each process has its own cache; with several API replicas, moving it to Redis would let them share it.

Embeddings blur exact identifiers; keyword search finds them but misses paraphrases. The Jaeger trace in the
[README screenshots](../README.md#screenshots) shows the cost: about 25 ms per search, not counting the network call to Voyage.

**Structured tables:**
- `list_endpoints` covers FastAPI, Flask, Express, NestJS, Spring and Go routers. Router prefixes from
  another file are recorded as `MOUNT` rows, so the agent can compose full paths.
- `list_dependencies` reads seven manifest formats.
- `find_symbol` and `find_imports` complete the set.

They answer "list all..." questions completely, which top-k retrieval can't guarantee.

**Code graph:**
- [graphify](https://github.com/safishamsi/graphify) (MIT) extracts files, symbols and their links:
  calls, references, imports, inheritance.
- Three tools use it: `architecture` (modules and most connected symbols), `related` (one symbol's
  links) and `impact` (everything that depends on it).
- Graph results are matched to our symbol table, so they cite full line ranges.
- The repo panel's Graph tab shows graphify's own interactive view of the whole graph. It is served
  sandboxed, so its scripts can't reach the API.

## LLM

- **Claude, through the Anthropic SDK.** The model is a setting (`AGENT_MODEL`).
- **Claude Opus 5.5** is the default when `AGENT_MODEL` is unset, for answer quality on multi-step
  investigations.
- **The demo runs Claude Haiku 5.5**, which `.env.example` sets: 40 times cheaper per token.
  Measured answers so far: $0.0011–$0.0065 each, 4–19 s.
  - Its weakness showed up while taking the screenshots: it sometimes cited a bare file name
    (`Visit.java:35-41`) or merged two locations into one bracket. Three fixes followed: the prompt
    now asks for full paths and one location per bracket; the UI splits merged citations; and the file
    endpoint opens a shortened path when it matches exactly one file.
- **Claude Sonnet 5.5** sits in between, and is the eval's judge. The runner warns if asked to let a
  model judge its own answers.

**Weighed:**
- **Other hosted models (OpenAI, Google Gemini):** could drive the same loop, but its request code
  calls Claude's API directly, with no framework in between, so switching means rewriting it. The
  eval could then compare them on the same 40 questions.
- **Open-weight models served ourselves:** no per-token cost and no data leaving the network, but GPU
  serving to run, and their multi-step tool use and citation discipline would need the eval first.
- **A provider-neutral layer (LiteLLM, or a framework's model wrapper):** swaps providers with one
  setting, but limits the loop to features every provider shares.

**Why Claude:** the loop relies on features of Claude's own API, listed under the request settings
below: adaptive thinking, prompt caching of the growing context, tool inputs streamed as they are
generated, and server-side fallback. The same models also run on Amazon Bedrock, Google Vertex AI and
Microsoft Foundry, so the cloud chosen for production doesn't change the model
([production](production.md)).

**Request settings:**
- Adaptive thinking at `medium` effort.
- Streaming, with tool inputs streamed as they are generated and validated with pydantic before any
  tool runs.
- Prompt caching.
- Server-side fallback when a model declines, on the models that support it.

**Prices** are kept in one table ([backend/app/pricing.py](../backend/app/pricing.py)), used for the
answer log and the eval.

Which model is good enough is what the eval is for: it can run the same 40 questions on Haiku and
Opus ([evaluation.md](evaluation.md)).

## Orchestration framework

**None.** The agent loop is about 250 lines on the Anthropic SDK ([backend/app/agent/loop.py](../backend/app/agent/loop.py)).
- The UI is built on the loop's events (tool started, tool finished with its sources, text, done
  with cost). They have to arrive in the right order and with the right detail.
- Prompt caching, validation of streamed tool inputs, refusals and model fallbacks all need direct
  control.
- The eval calls the same `answer_question` function the API does, so it measures the code that
  ships.

**Weighed:**
- LangChain/LangGraph and LlamaIndex would bring ready-made retrievers, but more layers between the
  code and the API, and less control over streaming and caching.
- The Claude Agent SDK or Managed Agents run a general agent with file and shell tools in a sandbox.
  That is powerful, but needs the repository in a sandbox and gives up the custom retrieval tools
  and UI events.

Background jobs use arq on Redis. The MCP server reuses the same tool functions.

## Prompt and context management

- **System prompt** ([backend/app/agent/prompts.py](../backend/app/agent/prompts.py)):
  - The rule that every statement must come from tool output.
  - Which tool to use for each kind of question.
  - The citation format, `[path:start-end]`.
  - What to do when the code doesn't contain the answer.

  A short block describes the repository (name, commit, size, languages). The code-graph guidance is
  added only when the repository has a graph.
- **Context budget per tool result:**
  - Any result is clipped at 12,000 characters, with a note saying how to narrow the call.
  - `read_file` returns at most 300 lines.
  - `search_code` returns at most 15 chunks, each previewed at 60 lines.
- **Step limit:** after 12 tool rounds, tools are switched off and the model must answer from what it
  has, saying what it couldn't verify.
- **Prompt caching:** tool definitions, the system prompt and the growing conversation are cached.
  In the traced answer, each call re-read the previous context from cache: 0, then 3.4k, 9.3k and
  12.5k cached tokens.
- **Conversation memory:** each conversation's full history, including tool results, is stored and
  replayed for follow-up questions. Nothing trims or summarises it yet (see [limitations](limitations.md)).
- **Single-call RAG mode** (`mode=rag`, used by the eval baselines): 8 reranked excerpts in one prompt.

## Guardrails

**Grounding:**
- The prompt requires every statement to come from tool output, with a citation.
- The UI makes uncited claims visible: they get a hollow marker, and a line under each answer counts
  how many points cite code ("15 of 22 points cite code").
- Four eval questions ask about features that don't exist. Answering them correctly means saying
  they don't exist.

**Tool safety:**
- All 11 tools are read-only queries over the index; the agent can't run code or reach the network.
- Tool inputs are validated (unknown fields rejected), and regexes are length-limited and checked
  before use.

**Budgets:** a 12-step limit, an output-token cap, and per-answer cost tracking.

**Ingestion:**
- Only https git URLs, plus local folders when `ALLOW_LOCAL_PATHS` is on.
- Zip entries can't write outside their folder.
- Size limits, and binaries are skipped.
- graphify runs with no API keys in its environment and a timeout.
- `READ_ONLY=true` turns a public demo into a read-only one.

**MCP over HTTP:** only accepts localhost `Host` and `Origin` headers (DNS-rebinding protection).

**Missing:** authentication, rate limiting, and screening of repository content for prompt injection.
The read-only tools limit what an injected instruction could do, but nothing detects one. All three
are listed under production work.

## Quality

**Evaluation** ([evaluation.md](evaluation.md)):
- **Questions:** 40, reviewed by hand, about two pinned repositories. Each lists the facts a correct
  answer must state and where the evidence is (84 locations).
- **Designs compared:** naive vector RAG, hybrid RAG, the agent, and the agent with the code graph.
- **Metrics:**
  - correctness (Sonnet 5.5 as judge for prose, exact matching for lists),
  - citation validity,
  - whether a citation hits the evidence,
  - retrieval recall,
  - latency and cost.
- **Self-checks:** an oracle answer scores 1.00 and an empty answer scores 0. A judge check confirms
  the judge fails empty and off-topic answers.
- **Approval gate:** changing any eval file blocks new runs until a person re-approves, so results
  from different eval versions never mix.
- **Results:** `evals.run summarize` prints a table comparing the designs on these metrics.

**Tests:** 80 backend tests: 79 pass, and one is skipped when an API key is set, because it checks
the missing-key error.
- **Unit tests:** tokenizer, chunker, extractors, query cache, zip extraction, graders, graph conversion,
  log format.
- **Integration tests** against a real Postgres and a fixture repository covering six web frameworks in
  four languages: indexing from a folder and from an uploaded zip, search modes, every tool, the agent
  loop with a scripted model, the MCP server, the answer log and stats endpoints, span nesting, and the
  graph view's sandbox headers.
- **Frontend:** strict TypeScript and ESLint, with no UI tests.

## Observability

Details in [observability.md](observability.md).
- **Answer log:** a row per answer from the web or MCP, with status, latency, time to first words,
  tokens, cost and each tool call. It is served by `/stats` and `/answers`, and shown in the Usage tab.
- **Structured logs:** JSON lines carrying a request ID and the trace ID.
- **Tracing:** OpenTelemetry spans for each answer (Claude calls with token counts, tool calls, search
  stages) and each indexing phase, viewed in Jaeger.

---

[README](../README.md#documentation) · Previous: [Architecture](architecture.md) · Next: [Key technical decisions](decisions.md)
