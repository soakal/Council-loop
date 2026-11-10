# Fable review: headless permission mode (round 1)
Verdict: APPROVE WITH FIXES.
Blocking: (1) council_state.py / discover_tests.py helpers missing from allowlist; (2) git is called as `git -C <TARGET> <sub>`, which prefix rules don't match; (3) missing git subcommands rev-parse, ls-files, branch --show-current, restore (+ cleanup); (4) set test_commands to the venv pytest so the Verifier doesn't try `python3 -m venv`; (5) drop pip.
Behavior (from memory, untested): subagents inherit parent permission mode and allow rules; acceptEdits approves in-cwd Edit/Write without the classifier; non-listed Bash in -p is denied, not hung.
Q1 acceptable for this repo (venv python = arbitrary code; real boundaries are branches, gate, no push). Q4 keep auto default, per-project option. Caveat: Shantry passed under auto today, so a pass there doesn't prove the fix; re-run a t09/t12-style edit to prove it. Flagged `𝜋thon` file in venv (checked: Python 3.14 venv symlink, harmless).
