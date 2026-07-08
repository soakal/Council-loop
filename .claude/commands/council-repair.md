---
description: Inspect and safely repair common Council Loop state problems.
argument-hint: "[--apply]"
allowed-tools: Read, Write, Edit, Bash
---

Repair request:

$ARGUMENTS

Do this safely:

1. Run `python3 scripts/council_doctor.py` and show the result.
2. Run `python3 scripts/council_state.py repair-history` first. If the user passed `--apply`, run `python3 scripts/council_state.py repair-history --apply`; otherwise do not rewrite files.
3. If `.council/state/stop.flag` exists, print its contents and classify it:
   - Ceiling stops may auto-resume when cycle headroom exists.
   - User stops, goal-complete stops, invalid config, and target repo safety stops require an explicit user action or a new `/goal`.
4. If the target repo is dirty, show `git -C <TARGET> status --short` and tell the user to commit, stash, unstage, or discard intentionally. Do not clean the target automatically.
5. Do not delete history, clear `stop.flag`, reset git state, or modify the target repo unless the user explicitly asks for that exact action.
6. End with the next safest command to run, such as `/council-doctor`, `/goal ...`, or `/loop /council-cycle`.
