import type { Source } from "./api";

// A bracket not already followed by a (link): [path/to/file.ext:12-30], [path:12], or several of them
// separated by commas or semicolons, as models sometimes merge them (the eval's grader reads them the same way).
const BRACKET = /\[([^[\]\n]{3,400})\](?!\()/g;
// One location: a path with an extension (or Dockerfile/Makefile/README), a line, an optional end line.
const REF = /^\s*`?([A-Za-z0-9_.@+\-/]+\.[A-Za-z0-9]+|[A-Za-z0-9_.@+\-/]*(?:Dockerfile|Makefile|README)):(\d+)(?:\s*[-–]\s*(\d+))?`?\s*$/;

export const CITE_PREFIX = "#cite=";

function citationLink(path: string, start: string, end?: string): string {
  const label = end ? `${path}:${start}-${end}` : `${path}:${start}`;
  return `[${label}](${CITE_PREFIX}${encodeURIComponent(path)}:${start}-${end ?? start})`;
}

/** Turn plain-text citations into Markdown links the renderer can intercept. A bracket holding several
 * locations becomes one link per location; a bracket with anything else in it is left alone. */
export function linkCitations(markdown: string): string {
  return markdown.replace(BRACKET, (whole, inner: string) => {
    const refs = inner.split(/[,;]/).map((part) => REF.exec(part));
    if (refs.some((ref) => ref === null)) return whole;
    return refs.map((ref) => citationLink(ref![1].replace(/^\.\//, ""), ref![2], ref![3])).join(" ");
  });
}

export function parseCitationHref(href: string): Source | null {
  if (!href.startsWith(CITE_PREFIX)) return null;
  const value = href.slice(CITE_PREFIX.length);
  const match = /^(.*):(\d+)-(\d+)$/.exec(value);
  if (!match) return null;
  return { path: decodeURIComponent(match[1]), start_line: Number(match[2]), end_line: Number(match[3]) };
}

export function sourceKey(s: Source): string {
  return `${s.path}:${s.start_line}-${s.end_line}`;
}

/** `src/a/b/OwnerController.java:12-30` -> `OwnerController.java:12-30` */
export function shortSource(s: Source): string {
  const name = s.path.split("/").pop() ?? s.path;
  return s.start_line === s.end_line ? `${name}:${s.start_line}` : `${name}:${s.start_line}-${s.end_line}`;
}

/** Same file and line ranges within `slack` lines of each other. */
export function sourcesOverlap(a: Source, b: Source, slack = 3): boolean {
  return a.path === b.path && a.start_line <= b.end_line + slack && b.start_line <= a.end_line + slack;
}

export function dedupeSources(sources: Source[]): Source[] {
  const seen = new Set<string>();
  return sources.filter((s) => {
    const key = sourceKey(s);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
