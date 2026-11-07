/** Display helpers for answer metrics (footer, Usage tab). */

/** "claude-haiku-5-5" -> "Haiku 5.5", "claude-opus-5" -> "Opus 5". Unknown ids are returned as-is. */
export function modelLabel(model: string | null | undefined): string {
  if (!model) return "Unknown model";
  const match = /^claude-([a-z]+)-(\d+)(?:-(\d+))?$/.exec(model);
  if (!match) return model;
  const [, family, major, minor] = match;
  return `${family[0].toUpperCase()}${family.slice(1)} ${major}${minor ? `.${minor}` : ""}`;
}

/** 840 -> "840 ms", 6120 -> "6.1 s", 95000 -> "1 min 35 s". */
export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes} min ${Math.round((ms % 60_000) / 1000)} s`;
}

/** Small costs keep enough digits to be meaningful: $0.0015, $0.42, $12.30. */
export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "–";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toPrecision(2)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatCount(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : Math.round(n).toLocaleString();
}
