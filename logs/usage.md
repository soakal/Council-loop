# Model usage per task

Approximate. Tokens are what the harness reports per subagent; "Codex" rows are the read-only reviewer.

| Task | Role | Model | Tokens (approx) | Notes |
|---|---|---|---|---|
| Plan | audit + PLAN.md | Fable 5.2 | ~146k | initial audit |
| Plan | Codex review | Codex CLI | ~30k | 11 findings, `reviews/codex-plan.md` |
| Plan | triage | Fable 5.2 | ~13k | 10 accepted, 1 partial |
| Plan | orchestration | Opus 5.5 (main) | — | session-level, not itemised |
| T0 | scaffolding | Opus 5.5 (main) | ~3k | written directly; too small for a subagent |
