# Model usage per task

Approximate. Tokens are what the harness reports per subagent; "Codex" rows are the read-only reviewer.

| Task | Role | Model | Tokens (approx) | Notes |
|---|---|---|---|---|
| Plan | audit + PLAN.md | Fable 5.2 | ~146k | initial audit |
| Plan | Codex review | Codex CLI | ~30k | 11 findings, `reviews/codex-plan.md` |
| Plan | triage | Fable 5.2 | ~13k | 10 accepted, 1 partial |
| Plan | orchestration | Opus 5.5 (main) | — | session-level, not itemised |
| T0 | scaffolding | Opus 5.5 (main) | ~3k | written directly; too small for a subagent |
| T4 | implement | Opus 5.5 | ~76k | 7 tests pass |
| T2 | implement | Opus 5.5 | ~80k | 12 tests pass |
| T3 | implement | Opus 5.5 | ~114k | 12 tests + validate.sh pass |
| T1 | implement | Opus 5.5 | ~103k | 22 tests pass |
| T1–T4 | review | Fable 5.2 | ~124k | T2 approve; T1/T3/T4 changes sent back |
| T1–T4 | review fixes | Opus 5.5 | ~40k total (incremental) | 68 tests + validate.sh pass after merge |
| T5 | implement | Opus 5.5 | ~99k | 14 new tests, 82 total pass |
| T5 | review | Fable 5.2 | ~78k | CHANGES: 3 gate-gaming holes + half-merge path |
