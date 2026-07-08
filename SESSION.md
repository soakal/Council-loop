# Council Loop Session Summary

Date: 2026-07-08

This file summarizes the work completed in this session and how to use the project.

## What this project is

Council Loop is a Claude Code scaffold that runs an autonomous engineering cycle:

```text
Arbiter -> Engineer -> Realist -> commit on accept
```

It is intended to be opened from the `Council-loop` folder in Claude Code so the custom
slash commands and agents load.

## Main changes completed

### Portability and setup

- Added Linux/macOS helpers:
  - `set-target.sh`
  - `start-council.sh`
- Improved Windows launcher:
  - `start-council.cmd` now shows the effective `target_repo`, including local overrides.
- Updated docs:
  - `README.md`
  - `QUICKSTART.md`
  - `CLAUDE.md`

### Reliability hardening

- Added `scripts/council_state.py` for deterministic:
  - config validation
  - history counting
  - history appending
  - transcript writing
  - history repair
  - cycle-to-commit lookup
- Added `.gitignore` rules for generated Python bytecode and runtime transcript/repair files.
- Hardened `/council-cycle` around:
  - effective config loading
  - exact `GOAL COMPLETE` handling
  - dry-run behavior
  - deferred cleanup guidance
  - transcript creation

### New commands

- `/council-doctor`
  - Checks config, target repo, required files, tools, models, history, and test discovery.
- `/council-repair [--apply]`
  - Diagnoses state issues and can safely repair malformed history lines.
- `/council-rollback <cycle-or-sha>`
  - Reverts a council-created commit after clean-tree and commit-prefix checks.

### New config features

Added to `.council/config.json` and `.council/config.example.json`:

```json
{
  "dry_run": false,
  "open_pr": false,
  "transcripts": true,
  "test_commands": []
}
```

Also added:

- `.council/config.schema.json`
- `scripts/discover_tests.py`
- `scripts/council_doctor.py`
- expanded `scripts/validate.sh`

## How to use it in Claude Code

1. Open a terminal inside the downloaded `Council-loop` folder.

2. Start Claude Code from that folder:

   ```bash
   claude
   ```

   Or use the launcher:

   ```bash
   ./start-council.sh
   ```

   On Windows:

   ```powershell
   .\start-council.cmd
   ```

3. Point Council Loop at the repo you want it to work on:

   ```bash
   ./set-target.sh "/path/to/your/project"
   ```

   On Windows:

   ```powershell
   .\set-target.ps1 "C:\path\to\your\project"
   ```

4. In Claude Code, run:

   ```text
   /council-doctor
   ```

5. Set a goal:

   ```text
   /goal Fix login form validation. Acceptance: invalid emails show an error, blank passwords are rejected, and tests pass.
   ```

6. Run the loop:

   ```text
   /loop /council-cycle
   ```

7. Check status:

   ```text
   /council-status
   ```

8. Stop cleanly:

   ```text
   /stop
   ```

## Safe dry-run mode

To practice without modifying files, add this to `.council/config.local.json`:

```json
{
  "target_repo": "/path/to/your/project",
  "dry_run": true
}
```

Then run:

```text
/goal Your task. Acceptance: what done looks like.
/loop /council-cycle
```

## Validation run

Validation passed on `main` before the latest merge:

```bash
./scripts/validate.sh
python3 scripts/council_doctor.py --root .
git diff --check
```

Expected doctor warnings in the cloud environment:

- `target_repo` points at the Council Loop repo itself for demo/self-hosting.
- No common project test command is present in this scaffold.
- `claude` may not be on PATH in non-interactive cloud shells.

## Current branch status at completion

The feature work was merged into and pushed to:

```text
main -> origin/main
```

Latest notable commits:

```text
dfb4fc7 Add council operations features
7310cf6 Ignore generated Python bytecode
3008781 Harden council runtime state handling
1bb2da3 Audit helper script validation
f071f6c Improve council loop portability and guardrails
```

## Important note

This is primarily for Claude Code. It can be used from Cursor's terminal by running
`claude` inside the `Council-loop` folder, but it is not a native Cursor extension.
