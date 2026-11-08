# How I used AI tools

- I used Claude Code in the desktop app, with Opus 5.5, to brainstorm the system and break it into a multi-step plan. I set the scope: Claude API, FastAPI and Next.js, repos up to about 500k lines, two to three days, demo on PetClinic and the FastAPI template.
- I wrote skills so an orchestrator could run several monitored subagents at once, each on a different task. Plan mode came before the observability work, and I approved that plan before any of that code was written.
- I used Claude Design to brainstorm the UI and build the investigation trail, the timeline of tool calls beside the answer. I picked that trail and the evidence rail from several options.
- Decisions stayed with me: I reviewed and approved all 40 eval questions, and only I can run `evals.run approve`. I asked before any paid run, chose Haiku for the demo after the cost and quality trade-off, and chose Jaeger and the Usage tab for observability.
- Two rules from the start: no AI attribution in commits or files, and I commit myself.
- Tests were the contract. I checked claims against the running system. Graphify "missing" calls through objects was true of 0.9.8; the installed release resolved them, so I corrected the claim and pinned that release.
- Testing also caught a wrong gold-location count in the README (95 vs 84), httpx instrumentation that recorded nothing, and Haiku shortening citations, which showed up while taking screenshots.

---

[README](../README.md#documentation) · Previous: [Engineering standards](engineering-standards.md) · Next: [What I'd do differently](with-more-time.md)
