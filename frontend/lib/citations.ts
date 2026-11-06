import type { Source } from "./api";

// [path/to/file.ext:12-30] or [path/to/file.ext:12], not already followed by a (link).
const CITATION = /\[([A-Za-z0-9_.@+\-/]+\.[A-Za-z0-9]+|[A-Za-z0-9_.@+\-/]*(?:Dockerfile|Makefile|README)):(\d+)(?:-(\d+))?\](?!\()/g;

export const CITE_PREFIX = "#cite=";

/** Turn plain-text citations into Markdown links the renderer can intercept. */
export function linkCitations(markdown: string): string {
  return markdown.replace(CITATION, (_match, path: string, start: string, end?: string) => {
    const label = end ? `${path}:${start}-${end}` : `${path}:${start}`;
    return `[${label}](${CITE_PREFIX}${encodeURIComponent(path)}:${start}-${end ?? start})`;
  });
}

export function parseCitationHref(href: string): Source | null {
  if (!href.startsWith(CITE_PREFIX)) return null;
  const value = href.slice(CITE_PREFIX.length);
  const match = /^(.*):(\d+)-(\d+)$/.exec(value);
  if (!match) return null;
  return { path: decodeURIComponent(match[1]), start_line: Number(match[2]), end_line: Number(match[3]) };
}
