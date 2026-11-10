# Codex review of PLAN.md — triage (Fable)

Checkpoint 1 (after PLAN.md, before owner approval). Raw output: `reviews/codex-plan.raw.md`.
Result: 10 accepted, 1 accepted in part, 0 rejected outright, 2 follow-up questions flagged for the owner. No task added; effort 20 h → 22 h.

| # | Sev | Decision | Reason / where it landed in PLAN.md |
|---|---|---|---|
| 1 | High | ACCEPT | Claim correct: rename-and-recheck can still take a live lock, and `begin-cycle`'s PID is dead at once (`council_state.py:281-285`). Replaced with OS-held locks (`fcntl` / `msvcrt`) for the task store and a runner-lifetime run lock; runner removes provably orphaned `cycle.lock`. §3 Lock, Run lock; T2, T6. Manual-`/loop` cycle lock left as is → §9. |
| 2 | High | ACCEPT | Claim correct: the model could edit `tasks.json` or justify its own test change. Runner now freezes verify inputs at claim and blocks on any change; test/config changes block until a reviewer approves; model-written reasons no longer count; hook denies edits to the task list and config. §3 Verification gate, No test gaming; T5. Residual (same OS user, no sandbox) stated and listed in §9. Owner question 6 added. |
| 3 | High | ACCEPT | Claim correct: no state between merge and `done`. Added `verified_sha`, restart reconcile via `merge-base --is-ancestor`, conflict → `blocked`, re-verify on merged tree when base moved. §3 Auto-resume, Git hygiene; T5, T6. |
| 4 | High | ACCEPT | Claim correct: only the `claude` process was covered. Sessions and checks run in their own process group; whole tree killed and reaped before recovery. §3 Outer loop; T6 (test asserts the child is gone). |
| 5 | High | ACCEPT | Claim correct: `council-cycle.md:16` allows `target_repo` ≠ project. Loop mode now requires `target_repo` = `"."`. §3 Git hygiene; T3. Flagged to owner as question 7 (old nexus runs used a separate target). |
| 6 | Med | ACCEPT | Claim correct. Loop mode refuses `dry_run: true` and `auto_commit: false`; gate needs a clean worktree and runs on the committed tip. §3 Verification gate; T3, T5. |
| 7 | Med | ACCEPT | Claim correct: `council-cycle.md:24` stops on any untracked file. `loop init` excludes all runtime paths. §3 Task store; T8 (test asserts empty `git status --porcelain`). |
| 8 | Med | ACCEPT IN PART | Stash SHA + task id now recorded on the task and shown in `loop status` (§3 Auto-resume; T6, T8). Auto-restore REJECTED: the council never builds on unreviewed residue from a dead cycle (`council-cycle.md:24`); re-applying it would trip that same guard. Listed in §9. |
| 9 | Med | ACCEPT | Claim correct. One monotonic deadline; every timeout and sleep clamped to time left. §3 Caps; T6. |
| 10 | Med | ACCEPT | Claim correct. `loop add` rejects unknown/circular dependencies; tasks behind a failed or blocked dependency become `blocked`. §3 Task store, Blocked; T2. |
| 11 | Med | ACCEPT | Claim correct. Hook now covers Read / Grep / Glob / NotebookEdit with real-path checks and is described as advisory. §3 Deny-list hook; T1. |

Flagged for owner (PLAN.md §8): question 6 (how strict test-change approval should be) and question 7 (loop mode limited to `target_repo` = `"."`).
