# What I'd do differently with more time

- Index by commit, not in place. A search during a re-index can see a half-written index. I would write each commit as its own snapshot and switch over when it is done, then only re-index files a push webhook says changed.
- Trim long chats. Turns are kept in full until they hit the context limit. I would summarise older turns once a conversation passes a set size.
- Add auth, a spend cap, and a public read-only demo. The answer log already records cost. There is no sign-in, no per-user limit, and no deployed demo. `READ_ONLY=true` is the path for a public one.
- Add CI and frontend tests. Backend tests run against Postgres. Nothing checks lint, types, or the evidence rail and investigation trail, which is where citation slips show up.
- Resolve endpoint prefixes, and record the demo. Prefixes that come from settings stay as `MOUNT` rows. The README still says the demo video is to be added.

---

[README](../README.md#documentation) · Previous: [How I used AI tools](ai-tools.md) · Next: [Known limitations](limitations.md)
