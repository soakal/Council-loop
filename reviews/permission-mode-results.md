# Trial results: acceptEdits + allowlist (2026-11-10, attended)

Config as in permission-mode-reconcile.md "Final allowlist" + round 3 (date added). Shantry backup: .council/config.json.bak-trial.

| Run | Project | Result | permission_denials | Cost |
|---|---|---|---|---|
| t002 /robots.txt | ~/repos/"Shantry Bills" (loop-test) | done, merged e4a98b0; pytest 26 passed | [] in both sessions | $1.10 |
| t001 README FAQ (t12-style) | disposable Council-loop clone (no remote, branch work) | done, merged afc591e; validate.sh pass, 189 OK | 1: Engineer's `cat >> README.md <<'EOF' ... ; grep ...` Bash refused; it then used Edit and succeeded | $1.54 |
| t003 "Due this month" | Shantry | done, merged e004735; council relabelled the existing unpaid total "Left:" -> "Due this month:" + added a test; pytest 27 passed | [] | $1.16 |

- No "no verdict" blocks in any session.
- Security: bandit ran on t002 (1 pre-existing B104, not in diff); pip-audit skipped every run.
- No deferred cycle happened, so cleanup of a new file under the allowlist is **untested**.
- `main` SHAs (Shantry, Council-loop) and `git ls-remote origin` for both: identical before/after (refs-before/after diff empty, apart from Council-loop's PR branch moving with my own review commit 41b0c70 before the trial).

Doc changes: reviews/permission-mode-docs.diff (README new section "Unattended edits", troubleshooting row, LATER.md line, REPORT.md limitation 1 + to-do 3). validate.sh passes.
