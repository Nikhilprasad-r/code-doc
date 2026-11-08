# Known limitations

- **Citation slips on smaller models.** Haiku sometimes cites a bare file name or merges locations.
  The UI copes with both: it opens a shortened path when it matches exactly one file, and splits
  merged citations. An ambiguous name like `schema.sql` lists its candidates instead. The eval's
  `cite_valid` metric deliberately stays strict and still counts shortened paths as invalid, so it
  keeps measuring citation discipline.
- **Endpoint extraction is pattern-based.** Prefixes set in another file or from configuration (such
  as `prefix=settings.API_V1_STR`) appear as `MOUNT` rows instead of being resolved.
- **Syntax-aware chunking covers five languages** (Python, JavaScript, TypeScript, Java, Go). Other
  languages are split by lines; the code graph covers more of them.
- **The code graph finds calls by static analysis.** Calls through untyped variables, callbacks or
  framework wiring can be missing; the tools say so and point to `grep`.
- **Conversations are never trimmed,** so very long ones grow until they hit the context limit.
- **Re-indexing replaces rows in place;** a search during a re-index can see a partial index.
- **No authentication or rate limiting.** Deploy only behind your own auth, with `READ_ONLY=true` for
  a public demo.
- **Version pins:** `tree-sitter` is pinned below 0.26 (0.26.0 segfaults on linux/aarch64), and
  graphify to exactly 0.9.95.
- **Local embeddings need an Apple Silicon Mac,** and the server must be running before indexing
  or searching. A repository indexed with one embedding model gets no vector search under another
  until it is re-indexed.
- **Port:** the database is published on port 5433, to avoid clashing with a local Postgres.

---

[README](../README.md#documentation) · Previous: [What I'd do differently](with-more-time.md)
