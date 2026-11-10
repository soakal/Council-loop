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
| T5 | review fixes | Opus 5.5 | ~19k (incremental) | 88 tests pass; merged d53db6e |
| T6 | implement | Opus 5.5 | ~163k | 18 new tests, 106 total pass |
| T6 | review | Fable 5.2 | ~107k | CHANGES: 7 required (no-work done, orphaned session on Ctrl-C, cap override, excludes, permission mode, limit-as-success, crash escalation) |
| T6 | review fixes | Opus 5.5 | ~42k (incremental) | 119 tests pass. Test change: own T6 assertion `tasks/`→`/tasks/` (anchoring required by Fable #4; stricter, not weaker) |
| T7 | implement | Opus 5.5 | ~80k | 5 new tests, 124 total pass |
| T8 | implement | Opus 5.5 | ~127k | 12 new tests, 131 total pass |
| T7+T8 | review + Codex triage | Fable 5.2 | ~126k | T7 CHANGES (2 prompt edits), T8 CHANGES (model can self-approve via CLI). Reviewer also ran 1 real claude -p session by accident (~$0.47): proved BLOCKED path end-to-end |
| Core | Codex checkpoint | Codex CLI | ~64k | 9 findings (5 high), `reviews/codex-core.md` |
| T7 | review fixes | Opus 5.5 | ~3k (incremental) | 127 tests pass |
| T8 | review fixes | Opus 5.5 | see T8 row | Test changes: own T8 tests — "unblock twice" now expects no-op (Fable nit); setUp strips COUNCIL_LOOP_RUN so CLI tests run inside a loop session. Flaky test_timeout_kills_child_tree → fixed in T8b |
| T8b | implement (Codex fixes + flaky test) | Opus 5.5 | ~152k | 162 tests ×5 runs pass. Test change #10: tree-kill test read /proc twice (race) → single read, ProcessLookupError/FileNotFound/Z = gone, 5 s bound; still fails with killpg disabled |
| T8b | review | Fable 5.2 | ~84k | 1 required (Windows taskkill pid reuse), test #10 change approved as not weakening |
| T8b | review fixes | Opus 5.5 | ~10k (incremental) | 165 tests pass ×2 |
| Dogfood run 1 | loop run (t09) | council (opus) via claude -p | 188k / $1.06 | blocked: auto-mode classifier "no verdict" on Engineer Write/Edit |
| Dogfood run 2 | loop run (t09) | council (opus) via claude -p | see status | edits allowed via loop.allowed_tools; classifier still refused Write of new loop_notify.py (credential+network code) |
| T10w | Windows fixes | Opus 5.5 | pending | from Tiny11 run: 1 fail, 33 errors |
| T9 | implement (fallback: classifier refused twice) | Opus 5.5 | pending | built outside loop on task branch; loop gates + merges |
| T10w | Windows fixes | Opus 5.5 | ~101k | Tiny11 rerun: 166 OK (skipped=4); Linux 166 OK. Test changes: cleanup ordering (temp dir deleted after holders killed), killpg create=True (Windows os lacks it), quoted -C path (bash strips backslashes) |
| T9 | implement (fallback) | Opus 5.5 | ~109k | 175 tests pass; notify.enabled default false |
| Windows | Tiny11 test runs ×2 | orchestrator | — | run1 3e0b7b3: 1 fail/33 err; run2 9139f8e: 166 OK |
| T9+T10w | review | Fable 5.2 | see notification | T10w APPROVE incl. 3 test changes; T9 CHANGES (id:null not paged, test_cli hits real secret) |
| T9 | review fixes | Opus 5.5 | ~12k (incremental) | 178 tests; T9+T10w Fable review ~91k |
| t10 | review (loop-built) | Fable 5.2 | see notification | APPROVE; validate.sh change approved via loop resume --approve-tests |
| Dogfood runs 3–4 | loop run | council via claude -p | 4 sessions $4.77 (run 4) | t09 done+merged by gate; t10 blocked (test change → approved); t11 blocked (classifier) |
| Dogfood run 5 | loop run | council via claude -p | 2 sessions $1.85 | t10 done+merged by gate; t11 blocked 2nd time (Verifier new-file Write refused) → fallback Opus build |
| t10 review | Fable 5.2 | ~67k |
| T11 | finish (fallback: Verifier Write refused ×2) | Opus 5.5 | ~91k | 182→184 tests; test change: 2 test_runner assertions updated to /dir/* exclude pattern |
| T11 | review | Fable 5.2 | ~66k | APPROVE; + orchestrator 2-line fix (doctor key fetch only when notify enabled) |
| Dogfood run 6 | loop run | council via claude -p | 2 sessions $2.24 | t11 done+merged by gate; t12 blocked: classifier no verdict on README edit (not content-specific) |
| T12 | docs (fallback: classifier no verdict on README) | Sonnet 5.5 | ~139k | 185 tests; validate.sh README coverage check |
| T12 | review | Fable 5.2 | see notification | CHANGES (3 doc accuracy fixes); validate.sh check negative-tested |
| Final | Codex checkpoint | Codex CLI | ~43k | 6 findings (2 high) |
| T12 | review fixes | Sonnet 5.5 | ~3k | ; orchestrator 1-line doctor sys.path fix found on Windows |
| Final | Codex triage | Fable 5.2 | ~91k | 4 accept, 1 part, 1 reject |
| T12b | final Codex fixes | Opus 5.5 | ~108k | 189 tests; Fable APPROVE (~72k) |
| Dogfood run 7 | loop run (t12) | council via claude -p | 1 session $0.64 | t12 done+merged by gate |
| **Loop total** | 7 runs | council via claude -p | 12 sessions, ~2.9M tokens, **$12.28** | from `loop status` (cost_usd in logs/loop.jsonl) |
| Report | write | Fable 5.2 | ~90k | REPORT.md |
