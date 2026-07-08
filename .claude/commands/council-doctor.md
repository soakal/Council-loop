---
description: Run Council Loop health checks for config, target repo, tools, state, models, and test discovery.
allowed-tools: Read, Bash
---

Run a concise health check:

1. Execute `python3 scripts/council_doctor.py` from the Council Loop project directory.
2. Print the output exactly enough for the user to see each `OK`, `WARN`, and `FAIL`.
3. If any `FAIL` appears, end with the single highest-priority fix to make before running `/loop /council-cycle`.
4. If only `WARN` entries appear, explain that the loop can run but the warnings should be reviewed.
5. If all checks are `OK`, say the council is ready to run.
