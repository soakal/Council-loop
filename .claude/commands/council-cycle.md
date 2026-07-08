---
description: Run ONE council cycle — Arbiter plans, Engineer implements, Realist reviews, commit on accept. Drive it with /loop /council-cycle for autonomous iteration.
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Task, Agent
---

Run **one** iteration of the council loop, then finish (do NOT loop yourself —
`/loop` re-invokes this command). Be terse throughout; this runs unattended.

Paths below are relative to this Council Loop project directory.

## 0. Preflight
- Load and validate the effective config first: run `python3 scripts/council_state.py effective-config` from this Council Loop project directory. If it fails, write `stop.flag` = the helper's `invalid council config: <brief reason>` message, print it, and STOP. Use the helper's JSON output as `config`. Resolve **TARGET** = `config.target_repo`; if it is `"."`, TARGET is this project directory. **All code changes and commits happen in TARGET.**
- If `.council/state/stop.flag` exists, classify its contents:
  - **Ceiling reason** (`max_cycles reached (N)` or `max_minutes reached`): get `cycles_done` with `python3 scripts/council_state.py history-count`. If `cycles_done < config.ceiling.max_cycles` (headroom remains) → delete `stop.flag`, set `started_at` in `.council/state/goal.md` to now (fresh minutes window), print a one-line `resuming — ceiling had headroom (cycles_done/max_cycles)` note, and continue preflight below. Otherwise (no headroom) → print the flag's contents and **STOP immediately**.
  - **Any other reason** (`user requested stop`, `goal complete`, `no goal set`, `target_repo is not a git repository`, `target repo has uncommitted changes...`, or anything unrecognized) → print its contents and **STOP immediately**; `/goal` remains the full reset path for these.
- Verify TARGET is a git repository (`git -C <TARGET> rev-parse --git-dir`). If not → write `stop.flag` = `target_repo is not a git repository`, print it, STOP.
- **First cycle only** (`python3 scripts/council_state.py history-count` returns 0): if `git -C <TARGET> status --porcelain` shows uncommitted changes, write `stop.flag` = `target repo has uncommitted changes — commit or stash them first`, print it, STOP. If `auto_commit:false` left staged-but-uncommitted work from an earlier run, the safe recovery is still for the user to commit or unstage it before `/goal`; never skip this guard and never sweep pre-existing work into a council commit.
- If `.council/state/goal.md` is missing → tell the user to run `/goal` first, write `.council/state/stop.flag` containing `no goal set`, and STOP.
- Read `.council/state/goal.md` (objective, acceptance criteria, `started_at`) and the last ~10 lines of `.council/state/history.jsonl` (treat a missing file as empty history).

## 1. Ceiling check
- `cycles_done` = output of `python3 scripts/council_state.py history-count`.
- If `cycles_done >= config.ceiling.max_cycles` → write `stop.flag` = `max_cycles reached (N)`, print it, STOP.
- Compute `elapsed_min` = now − `started_at` (Bash: `date -u +%s` vs the stored timestamp).
- If `elapsed_min >= config.ceiling.max_minutes` → write `stop.flag` = `max_minutes reached`, print it, STOP.
- `next_cycle = cycles_done + 1`.

## 2. Arbiter — plan (subagent `arbiter`)
Launch the **arbiter** subagent (Agent/Task tool), passing `config.models.arbiter` as the model override if set (the agent frontmatter is the fallback). Pass it: the objective + acceptance criteria, the TARGET path, and a short digest of prior cycles from history. Ask for either the single next step in its STEP/WHY/FILES/VERIFY/RISK format or a standalone `GOAL COMPLETE` line with evidence.
- If the arbiter output contains a line that is exactly `GOAL COMPLETE` → record a `"verdict":"complete"` history line using `python3 scripts/council_state.py append-history --cycle <next_cycle> --step "goal complete" --verdict complete --commit null --notes "<arbiter evidence>"`, write `stop.flag` = `goal complete`, print a closing summary, STOP. Do not treat partial-line mentions of `GOAL COMPLETE` as completion.

## 3. Engineer — implement (subagent `engineer`)
Launch the **engineer** subagent (model override: `config.models.engineer`) with the arbiter's STEP and the TARGET path. It makes the minimal change and reports CHANGED/SUMMARY/VERIFY_RESULT/NOTES. It must not commit.

## 4. Realist — review, with bounded revise (subagent `realist`)
Launch the **realist** subagent (model override: `config.models.realist`) with the STEP, the acceptance criteria, and the engineer's report. It returns `VERDICT: ACCEPT` or `VERDICT: REVISE` + FIXES.
- If `REVISE` and revise budget remains (`config.revise_attempts`): send the FIXES back to a fresh **engineer** invocation, then re-run the **realist**. Repeat up to `revise_attempts` times.
- Outcome after this section is one of: `accept`, or `deferred` (still REVISE after the budget is spent).

## 5. Commit (only on ACCEPT)
- If the verdict is `accept` and `config.auto_commit` is true, commit in TARGET — but **never sweep build artifacts into the commit**:
  1. Inspect what would be staged: `git -C <TARGET> status --porcelain`.
  2. **Artifact guard.** Identify any changed paths matching common regenerable-artifact patterns that are NOT already gitignored by TARGET:
     `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `node_modules/`, `.venv/`, `venv/`, `dist/`, `build/`, `target/`, `*.class`, `*.o`, `*.log`, `.DS_Store`, `Thumbs.db`, `*.tmp`.
     - **Tracked-path exemption:** before skipping a matched path, check `git -C <TARGET> ls-files -- <path>`. Non-empty output means the path is already tracked (a real, intentionally-committed file that merely matches an artifact pattern) — commit it normally, do not skip it. Only matched paths with **empty** `ls-files` output (untracked) get skipped + warned.
     - If any untracked matches remain after the exemption: stage everything **except** those (e.g. `git -C <TARGET> add -A` then `git -C <TARGET> reset -- <artifact paths>`, or add only the real deliverable paths). Print a one-line **WARNING** listing the skipped artifacts and recommend adding them to TARGET's `.gitignore`.
     - If none: `git -C <TARGET> add -A`.
  3. Commit: `git -C <TARGET> commit -m "<commit_prefix> cycle <next_cycle>: <short step summary>"`.
  4. Capture the SHA: `git -C <TARGET> rev-parse --short HEAD`.
  - If, after the guard, there is nothing real to commit, treat the cycle as `deferred` with note `no changes produced`.
- If the verdict is `accept` and `config.auto_commit` is false, stage but do **not** commit:
  1. Run the same **artifact guard** as above (step 5.2) against `git -C <TARGET> status --porcelain`, so regenerable artifacts never get staged either.
  2. Stage the real deliverable paths only (`git -C <TARGET> add -A` then `reset` the artifact paths, same as the `true` branch) — skip the `git commit` and SHA-capture steps entirely.
  - If, after the guard, there is nothing real to stage, treat the cycle as `deferred` with note `no changes produced`.
  - Record this outcome in history (§6) with `"commit": null` and note `auto_commit off — staged, not committed`.
- On `deferred`: do not commit (and, for the `false` branch, do not stage). Revert the Engineer's residue so the tree is clean for the next cycle. Build the cleanup path set from the CHANGED paths reported by every engineer invocation this cycle, plus current worktree-only tracked changes from `git -C <TARGET> diff --name-only`, plus untracked paths from `git -C <TARGET> status --porcelain`. For each path, check `git -C <TARGET> ls-files -- <path>`: non-empty → `git -C <TARGET> restore --worktree -- <path>` (restores from the index, so staged-but-uncommitted work from an earlier ACCEPT under `auto_commit:false` is untouched); empty → the Engineer created it as a new untracked file, so delete it directly (e.g. `rm -f -- <path>`). Never use `git checkout -- .` or `git clean -fd` — both would destroy unrelated staged-but-uncommitted work.

## 6. Record + report
- Append exactly one JSON line with `python3 scripts/council_state.py append-history --cycle <next_cycle> --step "<short step>" --verdict <accept|deferred|complete> --commit <sha-or-null> --notes "<brief>"`. Do not hand-write JSON; the helper handles quoting and escaping.
- Print a 3–5 line summary: cycle number, the step, verdict, commit SHA, and cycles remaining (`max_cycles − next_cycle`).
- Finish. Do not start another cycle.
