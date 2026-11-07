"""Queries over the answer log behind GET /stats, GET /answers and GET /answers/{id}."""

from app.db import connection

_RECENT_COLUMNS = ("id, created_at, source, repo_id, mode, left(question, 200) AS question, status, error, "
                   "coalesce(models[cardinality(models)], requested_model) AS model, latency_ms, ttft_ms, steps, "
                   "tool_calls, tool_errors, cost_usd")


def _where(repo_id: int | None, days: int | None) -> tuple[str, list]:
    conds, params = ["TRUE"], []
    if days is not None:
        conds.append("created_at >= now() - make_interval(days => %s)")
        params.append(days)
    if repo_id is not None:
        conds.append("repo_id = %s")
        params.append(repo_id)
    return " AND ".join(conds), params


async def answer_stats(repo_id: int | None = None, days: int = 7) -> dict:
    """Totals, latency percentiles (successful answers only), spend by model, tool health, and a daily series."""
    where, params = _where(repo_id, days)
    async with connection() as conn:
        totals = await (await conn.execute(f"""
            SELECT count(*) AS answers,
                   count(*) FILTER (WHERE status = 'ok') AS ok,
                   count(*) FILTER (WHERE status = 'error') AS errors,
                   count(*) FILTER (WHERE status = 'refused') AS refused,
                   count(*) FILTER (WHERE status = 'cut_off') AS cut_off,
                   count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'ok') AS latency_p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'ok') AS latency_p95_ms,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE status = 'ok') AS ttft_p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE status = 'ok') AS ttft_p95_ms,
                   avg(tool_calls) FILTER (WHERE status = 'ok') AS avg_tool_calls,
                   coalesce(sum(input_tokens), 0) AS input_tokens,
                   coalesce(sum(output_tokens), 0) AS output_tokens,
                   coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens,
                   coalesce(sum(cache_write_tokens), 0) AS cache_write_tokens,
                   coalesce(sum(cost_usd), 0) AS cost_usd,
                   count(*) FILTER (WHERE cost_usd IS NULL AND steps > 0) AS unpriced
            FROM answer_runs WHERE {where}""", params)).fetchone()
        by_model = await (await conn.execute(f"""
            SELECT coalesce(models[cardinality(models)], requested_model) AS model, count(*) AS answers,
                   coalesce(sum(cost_usd), 0) AS cost_usd,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'ok') AS latency_p50_ms
            FROM answer_runs WHERE {where} GROUP BY 1 ORDER BY answers DESC""", params)).fetchall()
        tools = await (await conn.execute(f"""
            SELECT t->>'name' AS name, count(*) AS calls,
                   count(*) FILTER (WHERE (t->>'is_error')::boolean) AS errors,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY (t->>'ms')::float) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY (t->>'ms')::float) AS p95_ms
            FROM answer_runs, jsonb_array_elements(tools) AS t WHERE {where}
            GROUP BY 1 ORDER BY calls DESC""", params)).fetchall()
        per_day = await (await conn.execute(f"""
            SELECT date_trunc('day', created_at)::date AS day, count(*) AS answers,
                   count(*) FILTER (WHERE status <> 'ok') AS not_ok, coalesce(sum(cost_usd), 0) AS cost_usd
            FROM answer_runs WHERE {where} GROUP BY 1 ORDER BY 1""", params)).fetchall()
    for tool in tools:
        tool["error_rate"] = tool["errors"] / tool["calls"] if tool["calls"] else 0.0
    return {"days": days, "repo_id": repo_id, **totals, "by_model": by_model, "tools": tools, "per_day": per_day}


async def recent_answers(repo_id: int | None = None, limit: int = 50) -> list[dict]:
    where, params = _where(repo_id, None)
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT {_RECENT_COLUMNS} FROM answer_runs WHERE {where} ORDER BY created_at DESC LIMIT %s",
            [*params, limit])).fetchall()


async def get_answer(answer_id: int) -> dict | None:
    async with connection() as conn:
        return await (await conn.execute("SELECT * FROM answer_runs WHERE id = %s", [answer_id])).fetchone()
