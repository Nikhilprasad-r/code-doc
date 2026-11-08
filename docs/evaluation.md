# Evaluation

The eval measures whether the design choices in the [RAG and LLM approach](rag-and-llm.md) actually matter. It asks the same 40 questions of
four designs and grades every answer.

**Questions** ([backend/evals/CASES.md](../backend/evals/CASES.md)): 20 each about
`spring-projects/spring-petclinic` and `fastapi/full-stack-fastapi-template`, pinned to fixed commits.
They cover overview (4), where-is (11), how-does-it-work (11), endpoints (5), dependencies (5), and 4
questions about features the code does not have, where the right answer is "it isn't there". Each
question lists the key facts a correct answer must state and the gold `path:start-end` locations of the
evidence, all checked against the pinned code.

**Designs**, all served by the same `answer_question` entry point the API uses:

| | Design | Index | Retrieval | Answering |
|---|---|---|---|---|
| (a) | `naive-rag` | fixed 40-line windows | vector only | one model call |
| (b) | `hybrid-rag` | syntax-aware chunks | keyword + vector, reranked | one model call |
| (c) | `agent` | syntax-aware chunks + structured tables | 8 tools, as many calls as it needs | agent loop |
| (d) | `agent-graph` (shipped) | (c) + code graph | (c)'s tools + `architecture`, `related`, `impact` | agent loop |

**Metrics** (0–1, higher is better):

| Metric | How it is graded |
|---|---|
| `correct` | Share of key facts stated correctly. List questions (all endpoints, drivers, libraries): each expected route or package must appear. Others: `claude-sonnet-5-5` marks each fact correct, missing, or incorrect. |
| `cite_valid` | Share of `[path:lines]` citations that point at real lines of the indexed commit. |
| `gold_cited` | Whether a citation overlaps a gold location. |
| `found_gold` | Whether anything retrieved or read overlaps a gold location (retrieval recall). |

The runner also records latency, tool calls, and tokens, and the summary prices them into $/question,
including the judge. Results go to `.claude/hillclimb/code-qa/` (`baseline` = a, `v1` = b, `v2` = c,
`v3` = d), and
an HTML report is built from them.

**Harness checks.** An oracle (the gold facts with gold citations) and a null (empty answer) are run
through the same runner and graders. The null scores 0 on every metric, except `cite_valid` on the 4
"isn't there" questions, where citing nothing is correct. The oracle scores 1.00 on every
programmatically graded metric, which also confirms that all 84 gold locations resolve in the index.
`evals.run judge-check` verifies that the judge gives 0 to an empty answer, "I don't know", and an
off-topic answer, and full marks to the oracle answer.

**Running it** (from `backend/`, with both API keys in `.env`). Each `run` uses `AGENT_MODEL` unless you pass `--model`, for example `--model claude-opus-5-5`; run the same design on two models with `--variant` to compare them side by side.

```bash
uv run python -m evals.run prepare
```

```bash
uv run python -m evals.run judge-check
```

```bash
uv run python -m evals.run approve
```

```bash
uv run python -m evals.run run --design naive-rag
```

```bash
uv run python -m evals.run run --design hybrid-rag
```

```bash
uv run python -m evals.run run --design agent
```

```bash
uv run python -m evals.run run --design agent-graph
```

```bash
uv run python -m evals.run summarize
```

`approve` records a hash of the eval files (runner, graders, questions, pinned repos,
lockfile). Later runs refuse to start if any of them changed until someone reviews and re-approves,
so results from different eval versions are never mixed. Runs are resumable: re-running a command
skips answers already scored.
