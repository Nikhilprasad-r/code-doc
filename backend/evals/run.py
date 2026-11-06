"""Code-QA evaluation runner.

    uv run python -m evals.run prepare                       # index both repos at pinned commits (both chunkings)
    uv run python -m evals.run approve                       # a person records the reviewed eval files
    uv run python -m evals.run run --design naive-rag        # -> .claude/hillclimb/code-qa/baseline/
    uv run python -m evals.run run --design hybrid-rag       # -> .../v1/
    uv run python -m evals.run run --design agent            # -> .../v2/
    uv run python -m evals.run summarize                     # comparison table (quality, latency, cost)
    uv run python -m evals.run judge-check                   # judge must fail empty / "I don't know" / off-topic
    uv run python -m evals.run run --design null --no-judge  # harness checks -> .claude/hillclimb/code-qa-checks/
    uv run python -m evals.run run --design oracle --no-judge

Run from backend/. Results are appended per case and resumable at (case, rep): re-running the same
command skips what is already in results.jsonl. Attempts that produce no scorable answer (API errors
after retries, timeouts, a different model serving the request, grader failures) go to errors.jsonl,
never into the scores.
"""

import argparse
import asyncio
import hashlib
import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import anthropic

from app.agent.loop import make_client
from app.config import Settings, get_settings
from app.db import close_db, connection, init_db
from app.ingest.pipeline import index_repo
from app.retrieval.embeddings import get_embedder
from evals.designs import DESIGNS, Design, Outcome, run_design
from evals.grading import GraderError, citation_scores, found_gold, grade_items, judge_facts

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parents[1]
HILLCLIMB_DIR = REPO_ROOT / ".claude" / "hillclimb"
CASES_PATH = EVAL_DIR / "cases.jsonl"
REPOS_PATH = EVAL_DIR / "repos.json"
DEFAULT_JUDGE = "claude-sonnet-5-5"

# Files whose change means "a different eval": the gate refuses to run until a person re-approves.
HARNESS_PATHS = ["backend/evals/run.py", "backend/evals/grading.py", "backend/evals/designs.py",
                 "backend/evals/cases.jsonl", "backend/evals/repos.json", "backend/uv.lock"]

METRICS = [
    {"id": "correct", "label": "Correct", "kind": "float", "scale": 1},
    {"id": "cite_valid", "label": "Valid cites", "kind": "float", "scale": 1},
    {"id": "gold_cited", "label": "Cites gold", "kind": "float", "scale": 1},
    {"id": "found_gold", "label": "Found gold", "kind": "float", "scale": 1},
]
PERF_FIELDS = [{"id": "latency_s", "label": "Latency", "unit": "s"}, {"id": "tool_calls", "label": "Tool calls"},
               {"id": "cost_usd", "label": "Cost", "unit": "$"}]

# First-party $/MTok (input, output). Cache reads bill at 0.1x input, cache writes at 1.25x input.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
          "claude-fable-5-1": (10.0, 50.0), "claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0)}

RETRYABLE = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)


def load_cases(ids: str | None = None) -> list[dict]:
    cases = [json.loads(line) for line in CASES_PATH.read_text().splitlines() if line.strip()]
    if ids:
        wanted = {i.strip() for i in ids.split(",")}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def cost_usd(model: str | None, usage: dict) -> float | None:
    if not model or model not in PRICES or not usage:
        return None
    price_in, price_out = PRICES[model]
    return (usage.get("input_tokens", 0) * price_in
            + usage.get("cache_read_input_tokens", 0) * price_in * 0.1
            + usage.get("cache_creation_input_tokens", 0) * price_in * 1.25
            + usage.get("output_tokens", 0) * price_out) / 1_000_000


# --- harness gate ------------------------------------------------------------------------------

def harness_sha() -> str:
    digest = hashlib.sha256()
    for rel in sorted(HARNESS_PATHS):
        path = REPO_ROOT / rel
        digest.update(rel.encode())
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() if path.exists() else b"missing")
    return digest.hexdigest()


def load_state(flow_dir: Path) -> dict:
    path = flow_dir / "_state.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    state.update({"metrics": METRICS, "perf_fields": PERF_FIELDS, "harness_paths": HARNESS_PATHS,
                  "prices": {m: {"in": p[0], "out": p[1]} for m, p in PRICES.items()}})
    return state


def save_state(flow_dir: Path, state: dict) -> None:
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "_state.json").write_text(json.dumps(state, indent=2) + "\n")


def check_harness(flow_dir: Path, state: dict, approve: bool) -> None:
    sha = harness_sha()
    if state.get("harness_sha") == sha:
        return
    if approve:
        state["harness_sha"] = sha
        save_state(flow_dir, state)
        print(f"harness approved: sha256 {sha[:12]} over {', '.join(HARNESS_PATHS)}", file=sys.stderr)
        return
    if state.get("harness_sha") is None:
        print(f"No approved harness yet (sha {sha[:12]} over {', '.join(HARNESS_PATHS)}).\n"
              "Review the eval files, then run `python -m evals.run approve`.", file=sys.stderr)
    else:
        print(f"The eval harness changed since it was approved ({state['harness_sha'][:12]} -> {sha[:12]}).\n"
              "Review the diff, then run `python -m evals.run approve`.", file=sys.stderr)
    sys.exit(2)


# --- repositories ------------------------------------------------------------------------------

async def find_repo(name: str, chunking: str) -> dict | None:
    pin = json.loads(REPOS_PATH.read_text())[name]
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT * FROM repos WHERE source = %s AND chunking = %s AND (ref = %s OR commit_sha = %s) "
            "ORDER BY (ref = %s) DESC NULLS LAST, id LIMIT 1",
            [pin["url"], chunking, pin["commit"], pin["commit"], pin["commit"]])).fetchone()


async def prepare(settings: Settings) -> None:
    embedder = get_embedder(settings)
    if settings.offline_embeddings:
        print("WARNING: VOYAGE_API_KEY is not set, so indexes use the offline hashing embedder and vector "
              "search results are not meaningful. Set it in .env before running the paid eval.\n")
    pins = json.loads(REPOS_PATH.read_text())
    for name, pin in pins.items():
        for chunking in ("ast", "lines"):
            repo = await find_repo(name, chunking)
            if repo is None:
                label = name if chunking == "ast" else f"{name} (eval: line chunks)"
                async with connection() as conn:
                    repo = await (await conn.execute(
                        "INSERT INTO repos (name, source, ref, chunking) VALUES (%s, %s, %s, %s) RETURNING *",
                        [label, pin["url"], pin["commit"], chunking])).fetchone()
            elif repo["ref"] != pin["commit"]:
                async with connection() as conn:  # pin it, so later re-indexes stay on this commit
                    await conn.execute("UPDATE repos SET ref = %s WHERE id = %s", [pin["commit"], repo["id"]])
            current = (repo["status"] == "ready" and repo["commit_sha"] == pin["commit"]
                       and (repo["stats"] or {}).get("embed_model") == embedder.model)
            if not current:
                print(f"indexing {name} [{chunking}] ...", flush=True)
                await index_repo(repo["id"], settings, embedder)
            repo = await find_repo(name, chunking)
            stats = repo["stats"]
            print(f"  repo {repo['id']:>3}  {name} [{chunking}]  {repo['status']}  {repo['commit_sha'][:10]}  "
                  f"{stats.get('chunks')} chunks  embeddings: {stats.get('embed_model')}")


# --- running -----------------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def file_lines(repo_id: int) -> dict[str, int]:
    async with connection() as conn:
        rows = await (await conn.execute("SELECT path, line_count FROM files WHERE repo_id = %s", [repo_id])).fetchall()
    return {r["path"]: r["line_count"] for r in rows}


class CaseFailure(Exception):
    def __init__(self, cls: str, message: str, outcome: Outcome | None = None, attempts: int = 1):
        super().__init__(message)
        self.cls, self.outcome, self.attempts = cls, outcome, attempts


async def answer_with_retries(design: Design, case: dict, repo: dict, settings: Settings,
                              client: anthropic.AsyncAnthropic, timeout_s: float) -> tuple[Outcome, int]:
    for attempt in range(1, 4):
        try:
            outcome = await asyncio.wait_for(run_design(design, case, repo, settings, client), timeout_s)
            return outcome, attempt
        except TimeoutError as exc:
            raise CaseFailure("timeout", f"no answer within {timeout_s}s", attempts=attempt) from exc
        except RETRYABLE as exc:
            if attempt == 3:
                raise CaseFailure("serving_error", f"{type(exc).__name__}: {exc}", attempts=attempt) from exc
            await asyncio.sleep(min(60.0, 5 * 2 ** attempt) * random.uniform(0.5, 1.5))
    raise AssertionError("unreachable")


async def run_case(case: dict, rep: int, design: Design, repo: dict, lines: dict[str, int], settings: Settings,
                   client: anthropic.AsyncAnthropic, judge_model: str | None,
                   timeout_s: float) -> tuple[dict, Outcome]:
    outcome, attempts = await answer_with_retries(design, case, repo, settings, client, timeout_s)
    served = set(outcome.models)
    if design.calls_model and served != {settings.agent_model}:
        raise CaseFailure("served_model_mismatch",
                          f"requested {settings.agent_model}, served {sorted(served) or 'nothing'}", outcome, attempts)
    if design.calls_model and not outcome.stop_reason:
        raise CaseFailure("harness_error", "; ".join(outcome.errors) or "answer stream ended early", outcome, attempts)

    absent = case["type"] == "absent"
    cites = citation_scores(outcome.answer, lines, case["gold"], absent)
    grade: dict[str, float] = dict(cites["scores"])
    explanation: dict[str, str] = {}
    if not absent:
        grade["found_gold"] = found_gold(outcome.sources, case["gold"])
    judge_usage: dict = {}
    served_judge: str | None = None
    if case["grader"] == "items":
        grade["correct"], explanation["correct"] = grade_items(outcome.answer, case["key_facts"])
    elif not outcome.answer.strip():
        grade["correct"], explanation["correct"] = 0.0, "empty answer"
    elif judge_model:
        try:
            judged = await judge_facts(client, judge_model, case["question"], case["key_facts"], outcome.answer)
        except (GraderError, *RETRYABLE) as exc:
            raise CaseFailure("grader_error", str(exc), outcome, attempts) from exc
        grade["correct"], explanation["correct"] = judged.score, judged.explanation
        judge_usage, served_judge = judged.usage, judged.model

    return {
        "prompt_id": case["id"], "rep": rep, "prompt": case["question"],
        "tags": [case["type"], case["repo"].split("/")[1], case["grader"]],
        "grade": {m["id"]: grade[m["id"]] for m in METRICS if m["id"] in grade},
        "explanation": explanation,
        "model": outcome.models[-1] if outcome.models else design.mode,
        "latency_s": outcome.latency_s, "tool_calls": outcome.tool_calls, "usage": outcome.usage,
        "judge_model": served_judge, "judge_usage": judge_usage,
        "stop_reason": outcome.stop_reason,
        "status": "truncated" if outcome.stop_reason == "max_tokens" else "ok",
        "meta": {"design": design.name, "repo_id": repo["id"], "commit": repo["commit_sha"], "attempts": attempts,
                 "refused": outcome.refused, "citations": cites["citations"], "sources_consulted": len(outcome.sources),
                 "answer": outcome.answer},
    }, outcome


async def run(args: argparse.Namespace, settings: Settings) -> None:
    design = DESIGNS[args.design]
    if args.model:
        settings = settings.model_copy(update={"agent_model": args.model})
    judge_model = None if args.no_judge else args.judge_model
    if judge_model and judge_model == settings.agent_model:
        print(f"WARNING: the judge ({judge_model}) is the model under test.", file=sys.stderr)

    flow_dir = HILLCLIMB_DIR / design.flow
    state = load_state(flow_dir)
    if design.calls_model:  # harness checks (oracle/null) spend nothing and are exempt
        check_harness(flow_dir, state, args.approve_harness)
    state["reps"] = args.reps
    save_state(flow_dir, state)

    variant_dir = flow_dir / (args.variant or design.variant)
    (variant_dir / "traces").mkdir(parents=True, exist_ok=True)
    (variant_dir / "change.md").write_text(f"{design.description}\n\nDesign `{design.name}`, agent model "
                                           f"`{settings.agent_model}`, judge `{judge_model}`.\n")
    (variant_dir / "summary.json").write_text(json.dumps(
        {"description": design.description, "label": design.name, "target": "code"}) + "\n")
    results_path, errors_path = variant_dir / "results.jsonl", variant_dir / "errors.jsonl"
    done_keys = {(r["prompt_id"], r["rep"]) for r in read_jsonl(results_path)}

    cases = load_cases(args.cases)
    repos: dict[str, dict] = {}
    lines: dict[str, dict[str, int]] = {}
    for name in {c["repo"] for c in cases}:
        repo = await find_repo(name, design.chunking)
        if repo is None or repo["status"] != "ready":
            sys.exit(f"{name} [{design.chunking}] is not indexed; run `python -m evals.run prepare` first")
        if design.calls_model and repo["stats"].get("embed_model") != get_embedder(settings).model:
            sys.exit(f"{name} [{design.chunking}] was embedded with {repo['stats'].get('embed_model')}; "
                     "run `python -m evals.run prepare` to re-index with the current embedder")
        repos[name], lines[name] = repo, await file_lines(repo["id"])

    todo = [(c, r) for c in cases for r in range(args.reps) if (c["id"], r) not in done_keys]
    print(f"{design.name} -> {variant_dir.relative_to(REPO_ROOT)}: {len(todo)} to run "
          f"({len(cases) * args.reps - len(todo)} already done), model {settings.agent_model}, judge {judge_model}")
    if not todo:
        return
    client = make_client(settings)
    semaphore = asyncio.Semaphore(args.concurrency)
    progress = Counter()
    started = time.monotonic()

    async def one(case: dict, rep: int) -> None:
        async with semaphore:
            try:
                row, outcome = await run_case(case, rep, design, repos[case["repo"]], lines[case["repo"]], settings,
                                              client, judge_model, args.timeout_s)
                (variant_dir / "traces" / f"{case['id']}_rep{rep}.json").write_text(
                    json.dumps(outcome.trace, indent=1, ensure_ascii=False))
                append_jsonl(results_path, row)
                progress["ok"] += 1
            except CaseFailure as failure:
                outcome = failure.outcome
                append_jsonl(errors_path, {
                    "prompt_id": case["id"], "rep": rep, "class": failure.cls, "message": str(failure),
                    "attempts": failure.attempts, "model": (outcome.models[-1] if outcome and outcome.models else None),
                    "usage": outcome.usage if outcome else {}})
                progress["error"] += 1
            except Exception as exc:  # anything else is a harness bug: record it, keep the batch going
                append_jsonl(errors_path, {"prompt_id": case["id"], "rep": rep, "class": "harness_error",
                                           "message": f"{type(exc).__name__}: {exc}"})
                progress["error"] += 1

    async def report_progress() -> None:
        while True:
            await asyncio.sleep(30)
            finished = progress["ok"] + progress["error"]
            rate = (time.monotonic() - started) / max(finished, 1)
            line = (f"{finished}/{len(todo)} done ({progress['error']} errors), "
                    f"~{int(rate * (len(todo) - finished))}s left")
            print(line, flush=True)
            (variant_dir / "progress.txt").write_text(line + "\n")

    ticker = asyncio.create_task(report_progress())
    await asyncio.gather(*(one(c, r) for c, r in todo))
    ticker.cancel()
    print(f"finished in {time.monotonic() - started:.0f}s: {progress['ok']} scored, {progress['error']} errors")
    print_summary(flow_dir, [variant_dir.name])


# --- summary -----------------------------------------------------------------------------------

def mean_ci(values: list[float]) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    mean = statistics.fmean(values)
    half = 1.96 * statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else math.nan
    return mean, half


def per_case(rows: list[dict], metric: str) -> dict[str, float]:
    """Mean over status-ok reps for each case (the report builder's convention)."""
    values: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("status", "ok") == "ok" and metric in r["grade"]:
            values[r["prompt_id"]].append(r["grade"][metric])
    return {case: statistics.fmean(v) for case, v in values.items()}


def variant_dirs(flow_dir: Path) -> list[str]:
    if not flow_dir.exists():
        return []
    names = [p.name for p in flow_dir.iterdir() if p.is_dir() and (p.name == "baseline" or p.name[1:].isdigit())]
    return sorted(names, key=lambda n: -1 if n == "baseline" else int(n[1:]))


def print_summary(flow_dir: Path, names: list[str] | None = None) -> str:
    names = names or variant_dirs(flow_dir)
    out = ["| design | n | correct | valid cites | cites gold | found gold | latency p50 | tool calls | "
           "$/question | errors |", "|---|---|---|---|---|---|---|---|---|---|"]
    by_type_rows: dict[str, dict[str, float]] = {}
    for name in names:
        rows = read_jsonl(flow_dir / name / "results.jsonl")
        errors = read_jsonl(flow_dir / name / "errors.jsonl")
        if not rows:
            continue
        label = json.loads((flow_dir / name / "summary.json").read_text()).get("label", name)
        cells = []
        for metric in ("correct", "cite_valid", "gold_cited", "found_gold"):
            mean, half = mean_ci(list(per_case(rows, metric).values()))
            cells.append("–" if math.isnan(mean) else f"{mean:.2f} ± {half:.2f}" if not math.isnan(half) else f"{mean:.2f}")
        latencies = [r["latency_s"] for r in rows if r.get("latency_s")]
        costs = [(cost_usd(r.get("model"), r.get("usage", {})) or 0.0)
                 + (cost_usd(r.get("judge_model"), r.get("judge_usage", {})) or 0.0) for r in rows]
        measured = any(cost_usd(r.get("model"), r.get("usage", {})) is not None for r in rows)
        error_classes = Counter(e["class"] for e in errors)
        refused = sum(1 for r in rows if r["meta"].get("refused"))
        truncated = sum(1 for r in rows if r.get("status") == "truncated")
        notes = ", ".join(f"{n} {c}" for c, n in error_classes.items()) or "0"
        if refused or truncated:
            notes += f"; {refused} refused, {truncated} truncated"
        latency = f"{statistics.median(latencies):.1f}s" if latencies else "–"
        tool_calls = f"{statistics.fmean(r['tool_calls'] for r in rows):.1f}"
        cost = f"${statistics.fmean(costs):.4f}" if measured else "not measured"
        out.append(f"| {label} ({name}) | {len(per_case(rows, 'correct'))} | " + " | ".join(cells)
                   + f" | {latency} | {tool_calls} | {cost} | {notes} |")
        types: dict[str, list[float]] = defaultdict(list)
        scores = per_case(rows, "correct")
        for r in rows:
            if r["prompt_id"] in scores and r["rep"] == 0:
                types[r["tags"][0]].append(scores[r["prompt_id"]])
        by_type_rows[label] = {t: statistics.fmean(v) for t, v in types.items()}

    if by_type_rows:
        all_types = sorted({t for v in by_type_rows.values() for t in v})
        out += ["", "Correct by question type:", "",
                "| design | " + " | ".join(all_types) + " |", "|---|" + "---|" * len(all_types)]
        for label, values in by_type_rows.items():
            out.append(f"| {label} | " + " | ".join(f"{values[t]:.2f}" if t in values else "–" for t in all_types) + " |")
    text = "\n".join(out)
    print(text)
    return text


# --- judge self-check --------------------------------------------------------------------------

async def judge_check(settings: Settings, judge_model: str) -> None:
    """The judge must give 0 to an empty answer, "I don't know", and a confident answer to another question,
    and full marks to the oracle answer. Five cases x four answers = 20 small judge calls."""
    cases = [c for c in load_cases() if c["grader"] == "judge"][::7][:5]
    client = make_client(settings)
    off_topic = ("The application stores uploaded files in an S3 bucket configured in storage.yaml and "
                 "serves them through a CDN [config/storage.yaml:1-20].")
    failures = 0
    for case in cases:
        probes = {"empty": "", "dont_know": "I don't know.", "off_topic": off_topic,
                  "oracle": "\n".join(f"- {f}" for f in case["key_facts"])}
        for name, answer in probes.items():
            result = await judge_facts(client, judge_model, case["question"], case["key_facts"], answer)
            expected_ok = result.score == 1.0 if name == "oracle" else result.score == 0.0
            failures += not expected_ok
            print(f"{case['id']:6} {name:10} score {result.score:.2f}  {'ok' if expected_ok else 'UNEXPECTED'}")
    print("judge check passed" if not failures else f"judge check: {failures} unexpected scores")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare", help="index both repositories at their pinned commits")
    run_p = sub.add_parser("run", help="run one design over the cases")
    run_p.add_argument("--design", choices=sorted(DESIGNS), required=True)
    run_p.add_argument("--variant", help="report directory (default: the design's own)")
    run_p.add_argument("--model", help="agent model (default: AGENT_MODEL)")
    run_p.add_argument("--judge-model", default=DEFAULT_JUDGE)
    run_p.add_argument("--no-judge", action="store_true", help="skip judge-graded correctness (no judge calls)")
    run_p.add_argument("--reps", type=int, default=1)
    run_p.add_argument("--cases", help="comma-separated case ids (default: all)")
    run_p.add_argument("--concurrency", type=int, default=4)
    run_p.add_argument("--timeout-s", type=float, default=300.0)
    run_p.add_argument("--approve-harness", action="store_true",
                       help="record the current eval files as reviewed (for a person to pass, not automation)")
    sub.add_parser("approve", help="record the current eval files as reviewed (run by a person)")
    sum_p = sub.add_parser("summarize", help="print the comparison table across variants")
    sum_p.add_argument("--flow", default="code-qa", help="code-qa (designs) or code-qa-checks (harness checks)")
    check_p = sub.add_parser("judge-check", help="sanity-check the judge on known answers")
    check_p.add_argument("--judge-model", default=DEFAULT_JUDGE)
    args = parser.parse_args()

    settings = get_settings()

    async def go() -> None:
        if args.command == "summarize":
            flow_dir = HILLCLIMB_DIR / args.flow
            text = print_summary(flow_dir)
            (flow_dir / "comparison.md").write_text(text + "\n")
            return
        if args.command == "judge-check":
            await judge_check(settings, args.judge_model)
            return
        if args.command == "approve":
            flow_dir = HILLCLIMB_DIR / "code-qa"
            check_harness(flow_dir, load_state(flow_dir), approve=True)
            return
        await init_db()
        try:
            if args.command == "prepare":
                await prepare(settings)
            else:
                await run(args, settings)
        finally:
            await close_db()

    asyncio.run(go())


if __name__ == "__main__":
    main()
