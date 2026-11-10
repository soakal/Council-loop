# Reconcile: headless permission mode (round 2)

Both reviewers: APPROVE WITH FIXES (Fable: `reviews/fable-permission-mode.md` summary below; Astra: `reviews/astra-permission-mode.txt`).

## Agreed by both (applied)
- Keep `auto` as the Council-loop default. `acceptEdits` + allowlist is a documented, opt-in, per-project setting.
- Drop `pip` (no unattended installs). Preinstall scanners into the venv instead.
- Add the plugin helpers: `council_state.py` and `discover_tests.py` (the cycle can't start without them).
- Add the `git -C <TARGET> ...` spellings and `rev-parse`, `ls-files`.
- Non-allowlisted Bash in headless `-p` is denied, not a hang.
- Test plan: small task first, inspect session/subagent logs and stop.flag (not just top-level `permission_denials`), confirm `main` SHA and remote refs unchanged.

## Points where they differed, and my pick
1. **Venv python: blanket (Fable) vs module-only (Astra).** Pick **Astra**: `python -m pytest`, `-m bandit`, `-m pip_audit` only. Reason: a blanket rule allows `python -c`, which goes around both the list and the guard. If a role needs more, it gets denied and the task blocks safely; we widen after seeing what was denied.
2. **`find` / shell readers.** Pick **Astra on `find`** (drop it: `-exec`/`-delete`). Keep read-only `ls`, `cat`, `head`, `tail`, `wc`, `grep` (Fable). Roles also have Read/Grep/Glob.
3. **Cleanup commands.** Fable listed `checkout -- .` and `clean -fd`, but `commands/council-cycle.md` line 89 forbids both. It uses `git -C <TARGET> restore --worktree -- <path>` and `rm -f -- <path>` for new untracked files. Pick: allow `restore`. **Do not allowlist `rm`**. If cleanup of a new file is denied, the next cycle sees a dirty tree, writes stop.flag and the task blocks. That's a safe failure, and we'll see if it happens. `stash` not needed (the Verifier is told never to stash).
4. **`test_commands`** (Fable): set `"test_commands": ["/home/brian/.venvs/shantry/bin/python -m pytest -q"]` so the Verifier uses the venv and doesn't try `python3 -m venv`. Astra didn't object.
5. **Isolation (Astra: disposable clone/container for unattended).** Pick: this trial is **attended** (Brian is up, I watch it), on a branch, nothing pushed. Before any overnight/unattended use, run in a disposable clone. This goes into REPORT/README as a condition and into LATER.md as "container for unattended runs".
6. **Does a Shantry pass prove anything? (Fable caveat.)** Agreed: Shantry passed under `auto` today, so it's not proof. Add a second test in a **disposable clone of Council-loop** (scratchpad, no remote push) re-running a t12-style task that edits `README.md`. That's the edit `auto` blocked overnight. That clone is also the isolation Astra asked for.
7. The `𝜋thon` file in the venv: checked, it's a symlink to `python3` that Python 3.14's `venv` creates. Harmless.

## Final allowlist (Shantry)
```json
"permission_mode": "acceptEdits",
"test_commands": ["/home/brian/.venvs/shantry/bin/python -m pytest -q"],
"allowed_tools": [
  "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)",
  "Bash(git rev-parse*)", "Bash(git ls-files*)", "Bash(git branch --show-current*)",
  "Bash(git add*)", "Bash(git commit*)", "Bash(git restore*)",
  "Bash(git -C * status*)", "Bash(git -C * diff*)", "Bash(git -C * log*)", "Bash(git -C * show*)",
  "Bash(git -C * rev-parse*)", "Bash(git -C * ls-files*)", "Bash(git -C * branch --show-current*)",
  "Bash(git -C * add*)", "Bash(git -C * commit*)", "Bash(git -C * restore*)",
  "Bash(python3 /home/brian/repos/Council-loop/scripts/council_state.py *)",
  "Bash(python3 /home/brian/repos/Council-loop/scripts/discover_tests.py *)",
  "Bash(ls*)", "Bash(cat*)", "Bash(head*)", "Bash(tail*)", "Bash(wc*)", "Bash(grep*)",
  "Bash(/home/brian/.venvs/shantry/bin/python -m pytest*)",
  "Bash(/home/brian/.venvs/shantry/bin/python -m bandit*)",
  "Bash(/home/brian/.venvs/shantry/bin/python -m pip_audit*)"
]
```
(`permission_mode`, `allowed_tools` under `loop`; `test_commands` top-level. Preinstall `bandit pip-audit` into the venv. Back up config first.)
For the Council-loop clone: same list with `python3 -m unittest*` and `bash scripts/validate.sh*` instead of the venv entries.

## Test plan
1. Back up `.council/config.json`; confirm branch `loop-test`; record `git rev-parse main` and `git ls-remote origin`.
2. Shantry: one small task (not the "Due this month" one), `loop run --once`. Grep `logs/sessions/*` for `denied|permission|not allowed`, check stop.flag/escalations.
3. Council-loop disposable clone in scratchpad (remote removed): t12-style README edit task, `loop run --once`, same log checks.
4. Confirm `main` SHA and remote refs unchanged in both.
5. Only then the bigger Shantry task.
Rollback: restore the backed-up config. Tasks freeze permission settings at claim time, so finish or reset claimed tasks before rolling back.

Questions: APPROVE this final version, or list what's still wrong.

## Round 3 changes (Astra round 2: `reviews/astra-permission-mode-r2.txt`)
1. **Clock:** add `"Bash(date -u +%s)"` (§1, §3.7).
2. **The list is not an exclusive boundary.** Corrected: a command not on the list is denied *unless* the permission mode or other settings already approve it (`acceptEdits` auto-approves some filesystem commands). So leaving `rm` off does not guarantee deletions are refused.
3. **Cleanup limitation, written down:** without an `rm` rule, deleting a new untracked file on a deferred cycle (§5) or temp files (§6) may be refused. Then the automatic rollback is incomplete for new files: the next cycle sees a dirty tree, writes stop.flag and the task blocks. That's safe, but it isn't the promised automatic rollback. This goes in the README troubleshooting row. Test: watch both trial runs for a deferred cycle that created a file; if none happens, this is reported as not tested. I'm not adding `rm -f -- *`: an explicit allow rule would allow any path, not just the repo.
4. **Helper paths:** the runner passes `--plugin-dir PLUGIN_ROOT`, the Council-loop checkout the `loop` command runs from (`scripts/loop_runner.py`). Both tests run `~/repos/Council-loop/loop --project <dir>`, so `${CLAUDE_PLUGIN_ROOT}` = `/home/brian/repos/Council-loop` in both, which matches the rules. The clone is only the *target* project.
5. **Isolation wording corrected:** the disposable clone protects the original worktree only. It still runs as Brian with his credentials and filesystem. It is **not** the isolation needed for unattended runs. Unattended use stays off until a container/separate user exists (LATER.md item).
6. **Remote check:** record `git ls-remote https://github.com/soakal/Council-loop.git` and `git ls-remote origin` in the real Shantry repo before and after; the clone has no remote.
7. **test_commands** is a hint, not a guarantee. If the Verifier still tries `python3 -m venv`, it's denied (not on the list, not a file edit) and shows in the logs.
8. **Scanners (Fable round 2):** `agents/security.md` calls bare `bandit`/`pip-audit`, which aren't on PATH, so Security will probably still record them as skipped (same as today's Shantry run). Not changing the role prompt in this trial; check the Security `TOOLS:` line in history afterwards.

Fable round 2: APPROVE (picks confirmed; notes 1-3 folded in above as items 2, 4 and 8).
