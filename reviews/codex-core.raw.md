1. **High — `scripts/council_state.py:70`**: Protected paths omit `package.json`, `pyproject.toml`, and common test-runner configs; the model can replace `npm test` with a successful no-op or disable test discovery without triggering review. **Fix:** protect existing verification entry points and configuration.

2. **High — `scripts/loop_runner.py:169`**: Frozen inputs omit `loop.base_branch`; changing it mid-task passes tamper detection and redirects both protection comparisons and the eventual merge. **Fix:** freeze and compare the base branch and other execution-routing settings.

3. **High — `scripts/loop_runner.py:710`**: Holding `run.lock` does not prove previous sessions are dead: killing the runner leaves its separately grouped Claude process running, while recovery starts stashing and switching branches. **Fix:** track and terminate surviving session trees before recovery, using OS lifecycle containment where available.

4. **High — `scripts/loop_runner.py:122`**: Successful commands clean up surviving descendants only on POSIX; Windows background children can continue modifying files during verification, merge, or subsequent tasks. **Fix:** contain Windows subprocess trees in a Job Object and terminate remaining children on every exit path.

5. **High — `scripts/hooks/guard.py:293`**: `git push --repo origin HEAD:main` and `git push --repo origin +HEAD:work` bypass the main/force guards because both discard the first positional argument as a remote even when `--repo` supplied it. **Fix:** parse the remote separately and inspect every actual refspec.

6. **Medium — `hooks/hooks.json:22`**: The guard still launches through hard-coded `python3`, although Windows wrappers explicitly support installations exposing only `py` or `python`; on those machines the security hook cannot execute. **Fix:** use a cross-platform interpreter launcher and verify hook execution before starting sessions.

7. **Medium — `scripts/loop_runner.py:318`**: A frozen “no checks” result is falsy, so the gate discovers checks again after the model edits the repository; a task can acquire model-defined verification instead of blocking. **Fix:** distinguish an absent snapshot from a frozen empty check list and block the latter.

8. **Medium — `scripts/loop_runner.py:441`**: Failed verification output is persisted verbatim in task `reason`, bypassing log redaction; credentials printed by a check survive in `tasks.json` and are fed into the next model session. **Fix:** redact output before storing reasons or incorporating them into prompts.

9. **Medium — `scripts/loop_runner.py:710`**: Recovery also unconditionally deletes an active manual council session’s `cycle.lock`; manual sessions never acquire `run.lock`, so the runner can overlap them and stash their work. **Fix:** make manual cycles and runner startup share an ownership lock.
