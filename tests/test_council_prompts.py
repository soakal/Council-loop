"""Pins the council prompts to what loop_runner.py expects, so the two can't drift.

The runner-level mapping (stop.flag `blocked: x` -> task status blocked) is already
covered by tests/test_runner.py::test_blocked_flag_blocks_and_escalates_once.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import loop_runner  # noqa: E402


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class CouncilPromptTests(unittest.TestCase):
    def test_cycle_writes_blocked_flag(self):
        self.assertIn("`blocked: <reason>`", read("commands/council-cycle.md"))

    def test_cycle_has_headless_no_questions_rule(self):
        text = read("commands/council-cycle.md")
        self.assertIn("COUNCIL_LOOP_RUN=1", text)
        self.assertIn("NEVER ask the user a question", text)

    def test_arbiter_documents_blocked(self):
        self.assertIn("BLOCKED: <one-line reason>", read("agents/arbiter.md"))

    def test_goal_uses_set_goal(self):
        text = read("commands/goal.md")
        self.assertIn("council_state.py\" set-goal --objective", text)
        self.assertIn("init-config", text)

    def test_runner_flag_strings_appear_in_cycle(self):
        # Every stop.flag string the runner gates on must be one the council writes.
        text = read("commands/council-cycle.md")
        for flag in ("goal complete", *loop_runner.CEILING_FLAGS):
            self.assertIn(f"`{flag}", text, flag)

    def test_goal_single_quotes_set_goal_args(self):
        # Double quotes would let backticks / $(...) in the goal text run in the shell.
        text = read("commands/goal.md")
        self.assertIn("--objective '<objective>' --acceptance '<criteria, or empty>'", text)
        self.assertIn("'\\''", text)
        self.assertNotIn('--objective "<objective>"', text)

    def test_arbiter_ambiguity_block_is_narrow(self):
        text = read("agents/arbiter.md")
        self.assertIn("no reasonable default exists AND a wrong guess would be costly to undo", text)
        self.assertIn("state the assumption in WHY", text)

    def test_cycle_no_questions_rule_is_unconditional(self):
        self.assertIn("NEVER ask the user a question or wait for input during /council-cycle — block instead",
                      read("commands/council-cycle.md"))


if __name__ == "__main__":
    unittest.main()
