---
description: Revert a council-created commit by cycle number or commit SHA.
argument-hint: "<cycle-number|commit-sha>"
allowed-tools: Read, Bash
---

Rollback target:

$ARGUMENTS

Safely revert a council-created change:

1. If the argument is empty, ask for a cycle number or commit SHA and stop.
2. Load the effective config with `python3 scripts/council_state.py effective-config` and resolve TARGET. If config is invalid, print the error and stop.
3. Verify TARGET is a git repository and its working tree is clean. If dirty, print `git -C <TARGET> status --short` and stop; never rollback on top of uncommitted work.
4. If the argument is a number, resolve its commit with `python3 scripts/council_state.py lookup-commit --cycle <number>`. If the helper fails, print its message and stop.
5. If the argument looks like a SHA, use it directly after confirming `git -C <TARGET> cat-file -e <sha>^{commit}` succeeds.
6. Run `git -C <TARGET> log -1 --format=%s <sha>` and verify the subject starts with the configured `commit_prefix`. If not, stop; do not rely on judgment to revert non-council commits.
7. Run `git -C <TARGET> show --stat --oneline <sha>` so the user can see what will be reverted.
8. Revert with `git -C <TARGET> revert --no-edit <sha>`.
9. Print the new revert commit SHA and a short summary. Do not push unless the user explicitly asks.
