"""Claude API prices, used to put a dollar figure on every answer (app/observability) and eval run (evals/).

First-party $/MTok: (input, output, 5-minute cache write, cache read). The agent's cache_control uses the
default 5-minute TTL. The cache-read rate is not a fixed share of input (0.05x for Opus 5.5 and Sonnet 5.5,
0.025x for Fable 5.1, 0.1x for the others), so each one is listed. Haiku 5.5 is priced at its tier for
prompts up to 100k tokens; prompts above that cost 5x.
"""

PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 12.50, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 5.00, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-opus-5": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5": (2.0, 10.0, 2.50, 0.20),
}


def cost_usd(model: str | None, usage: dict) -> float | None:
    """Dollar cost of `usage` (Anthropic usage fields) on `model`, or None for an unknown model."""
    if not model or model not in PRICES or not usage:
        return None
    price_in, price_out, price_cache_write, price_cache_read = PRICES[model]
    return (usage.get("input_tokens", 0) * price_in
            + usage.get("cache_read_input_tokens", 0) * price_cache_read
            + usage.get("cache_creation_input_tokens", 0) * price_cache_write
            + usage.get("output_tokens", 0) * price_out) / 1_000_000
