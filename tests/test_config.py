import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import contextlib
import copy
import io
import json
import re
import tempfile
import unittest

import council_state


def base_config() -> dict:
    """A pre-loop-mode config (no loop block), as an older project would have."""
    data = json.loads((council_state.PLUGIN_ROOT / ".council" / "config.example.json").read_text(encoding="utf-8"))
    data.pop("loop")
    return data


class ConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".council").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_config(self, cfg: dict) -> None:
        (self.root / ".council" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def load(self) -> dict:
        # load_config prints its root/local-file notes to stderr; keep test output clean.
        with contextlib.redirect_stderr(io.StringIO()):
            return council_state.load_config(self.root)

    def run_cli(self, *argv: str) -> int:
        args = council_state.build_parser().parse_args(["--root", str(self.root), *argv])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return args.func(args)


class LoopBlockTests(ConfigTestCase):
    def test_defaults_applied_to_config_without_loop(self) -> None:
        self.write_config(base_config())
        self.assertEqual(self.load()["loop"], council_state.DEFAULT_LOOP)

    def test_partial_loop_block_keeps_values_and_fills_rest(self) -> None:
        cfg = base_config()
        cfg["loop"] = {"max_attempts": 5, "backoff": {"jitter": 0.5}}
        self.write_config(cfg)
        loop = self.load()["loop"]
        self.assertEqual(loop["max_attempts"], 5)
        self.assertEqual(loop["backoff"], {"base_seconds": 60, "max_seconds": 3600, "jitter": 0.5})
        self.assertEqual(loop["max_sessions"], 50)

    def test_bad_cap_rejected(self) -> None:
        for key, value in (("max_attempts", 0), ("max_sessions", -1), ("max_hours", 0), ("max_attempts", True)):
            cfg = base_config()
            cfg["loop"] = {key: value}
            self.write_config(cfg)
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, f"loop.{key}"):
                self.load()

    def test_unknown_loop_key_rejected(self) -> None:
        cfg = base_config()
        cfg["loop"] = {"max_atempts": 3}
        self.write_config(cfg)
        with self.assertRaisesRegex(ValueError, "max_atempts"):
            self.load()

    def test_bad_secrets_backend_rejected(self) -> None:
        cfg = base_config()
        cfg["loop"] = {"secrets": {"backend": "vault"}}
        self.write_config(cfg)
        with self.assertRaisesRegex(ValueError, "loop.secrets.backend"):
            self.load()

    def test_env_and_infisical_backends_accepted(self) -> None:
        for backend in ("env", "infisical"):
            cfg = base_config()
            cfg["loop"] = {"secrets": {"backend": backend}}
            self.write_config(cfg)
            with self.subTest(backend=backend):
                self.assertEqual(self.load()["loop"]["secrets"]["backend"], backend)


class LoopModeTests(ConfigTestCase):
    def good(self) -> dict:
        cfg = base_config()
        cfg["loop"] = copy.deepcopy(council_state.DEFAULT_LOOP)
        cfg["loop"]["base_branch"] = "revamp/console-loop"
        return cfg

    def test_compatible_config_has_no_errors(self) -> None:
        self.assertEqual(council_state.validate_loop_mode(self.good()), [])

    def test_each_incompatible_setting_reported(self) -> None:
        cases = (
            ("target_repo", "/elsewhere", "target_repo"),
            ("dry_run", True, "dry_run"),
            ("auto_commit", False, "auto_commit"),
        )
        for key, value, needle in cases:
            cfg = self.good()
            cfg[key] = value
            with self.subTest(key=key):
                errors = council_state.validate_loop_mode(cfg)
                self.assertEqual(len(errors), 1)
                self.assertIn(needle, errors[0])
        for branch in ("main", "master"):
            cfg = self.good()
            cfg["loop"]["base_branch"] = branch
            with self.subTest(base_branch=branch):
                errors = council_state.validate_loop_mode(cfg)
                self.assertEqual(len(errors), 1)
                self.assertIn("loop.base_branch", errors[0])

    def test_loop_check_exit_codes(self) -> None:
        self.write_config(self.good())
        self.assertEqual(self.run_cli("loop-check"), 0)
        cfg = self.good()
        cfg["dry_run"] = True
        self.write_config(cfg)
        self.assertEqual(self.run_cli("loop-check"), 1)


class SetGoalTests(ConfigTestCase):
    def state(self) -> Path:
        return self.root / ".council" / "state"

    def started_at(self) -> str:
        text = (self.state() / "goal.md").read_text(encoding="utf-8")
        return re.search(r"^started_at: (\S+)$", text, re.MULTILINE).group(1)

    def fake_run(self) -> None:
        (self.state() / "history.jsonl").write_text('{"cycle": 1}\n', encoding="utf-8")
        (self.state() / "dynamic-agents.jsonl").write_text("{}\n", encoding="utf-8")
        (self.state() / "transcripts").mkdir()
        (self.state() / "transcripts" / "cycle-0001.md").write_text("t", encoding="utf-8")
        (self.state() / "stop.flag").write_text("halt", encoding="utf-8")

    def test_first_goal_on_empty_state(self) -> None:
        self.assertEqual(self.run_cli("set-goal", "--objective", "do x", "--acceptance", "x done"), 0)
        text = (self.state() / "goal.md").read_text(encoding="utf-8")
        self.assertIn("## Objective\ndo x", text)
        self.assertIn("## Acceptance criteria\nx done", text)
        self.assertFalse((self.state() / "archive").exists())

    def test_archives_prior_run_and_second_set_goal_works(self) -> None:
        self.run_cli("set-goal", "--objective", "first")
        first = self.started_at()
        self.fake_run()

        self.assertEqual(self.run_cli("set-goal", "--objective", "second"), 0)
        archive = self.state() / "archive" / first.replace(":", "-")
        self.assertTrue((archive / "history.jsonl").is_file())
        self.assertTrue((archive / "dynamic-agents.jsonl").is_file())
        self.assertTrue((archive / "transcripts" / "cycle-0001.md").is_file())
        for name in ("history.jsonl", "dynamic-agents.jsonl", "transcripts", "stop.flag"):
            self.assertFalse((self.state() / name).exists(), name)
        self.assertIn("second", (self.state() / "goal.md").read_text(encoding="utf-8"))
        self.assertIn("unspecified", (self.state() / "goal.md").read_text(encoding="utf-8"))

        # Re-run with nothing new to archive: no-op archive, goal rewritten.
        self.assertEqual(self.run_cli("set-goal", "--objective", "third"), 0)
        self.assertEqual(len(list((self.state() / "archive").iterdir())), 1)

        # A third run sharing the same started_at name lands in a -2 folder, not on top.
        (self.state() / "goal.md").write_text(f"started_at: {first}\n", encoding="utf-8")
        self.fake_run()
        self.assertEqual(self.run_cli("set-goal", "--objective", "fourth"), 0)
        self.assertTrue((self.state() / "archive" / (first.replace(":", "-") + "-2") / "history.jsonl").is_file())
        self.assertTrue((archive / "history.jsonl").is_file())

    def test_empty_objective_rejected(self) -> None:
        self.assertEqual(self.run_cli("set-goal", "--objective", "  "), 1)
        self.assertFalse((self.state() / "goal.md").exists())


if __name__ == "__main__":
    unittest.main()
