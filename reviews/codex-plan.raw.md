1. **High — Lock fix remains unsafe** ([PLAN.md:115](/home/brian/repos/Council-loop/PLAN.md:115)). Rename-then-recheck can move a replacement owner’s lock; applying dead-PID detection to the short-lived `begin-cycle` helper also immediately invalidates live cycles. **Fix:** use OS-held locks and a persistent cycle owner.

2. **High — Verification inputs remain model-writable** ([PLAN.md:117](/home/brian/repos/Council-loop/PLAN.md:117)). The model can alter task commands, test configuration, or harnesses; writing its own justification also satisfies the proposed test-change gate. **Fix:** freeze verification inputs outside model control and require independent approval for changes.

3. **High — Merge and task completion lack crash recovery** ([PLAN.md:129](/home/brian/repos/Council-loop/PLAN.md:129)). A crash after merging but before recording `done` requeues an already-integrated task; merge conflicts and base movement are unspecified. **Fix:** persist merge intent and verified SHAs, reconcile on restart, and verify the resulting integration commit.

4. **High — Session timeout may leave writers alive** ([PLAN.md:120](/home/brian/repos/Council-loop/PLAN.md:120)). Terminating only `claude` can leave shell/test descendants modifying files while recovery stashes or retries. **Fix:** terminate and reap the entire process tree before releasing ownership or recovering.

5. **High — Branch operations may target the wrong repository** ([PLAN.md:129](/home/brian/repos/Council-loop/PLAN.md:129)). The existing cycle permits `config.target_repo` to differ from the project containing state; the plan does not reconcile that with runner branching and verification. **Fix:** resolve one canonical target for all Git operations, checks, and repository locks.

6. **Medium — `auto_commit:false` and `dry_run` break merge semantics** ([PLAN.md:129](/home/brian/repos/Council-loop/PLAN.md:129)). Both remain supported internally, but verification could pass against staged changes while the runner merges an unchanged branch. **Fix:** reject incompatible modes and require a clean worktree with the verified changes committed.

7. **Medium — Runtime files can block every cycle** ([PLAN.md:114](/home/brian/repos/Council-loop/PLAN.md:114)). Initialization excludes `tasks.json`, but not explicitly its sibling lock, temporary files, or target-project logs; the current preflight rejects untracked files. **Fix:** exclude every runtime path in the target repository before launching sessions.

8. **Medium — Resume preserves stashes but abandons their work** ([PLAN.md:119](/home/brian/repos/Council-loop/PLAN.md:119)). Recovery saves uncommitted progress without specifying restoration or associating the stash with the task. **Fix:** record the stash SHA and restore it on the same task branch, blocking on conflicts.

9. **Medium — Wall-clock cap is not strictly enforced** ([PLAN.md:121](/home/brian/repos/Council-loop/PLAN.md:121)). A session, verification command, or backoff started near the deadline can exceed twelve hours. **Fix:** clamp every timeout and sleep to the remaining monotonic run budget.

10. **Medium — Dependency deadlocks are undefined** ([PLAN.md:114](/home/brian/repos/Council-loop/PLAN.md:114)). Missing IDs, dependency cycles, and dependencies that fail can leave pending tasks permanently unclaimable. **Fix:** validate the dependency graph and explicitly block tasks whose prerequisites cannot complete.

11. **Medium — Credential protection omits direct read tools** ([PLAN.md:127](/home/brian/repos/Council-loop/PLAN.md:127)). The proposed matchers cover editing and Bash, allowing credential access through `Read`, `Grep`, or alternate tools; GitHub protection cannot prevent local disclosure. **Fix:** cover all file-access tools with canonical path checks and describe hooks as advisory protection.
