"""End-to-end: loop init -> add -> run against a fresh temp repo with a stub `claude`."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import loop_tasks  # noqa: E402

LOOP = Path(__file__).resolve().parents[1] / "scripts" / "loop.py"

# Same shape as FAKE in tests/test_cli.py: one finished cycle, then session JSON.
STUB = r'''
import json, subprocess, sys
from pathlib import Path
Path("e2e.txt").write_text("done\n")
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "council: cycle 1"], check=True)
Path(".council/state/stop.flag").write_text("goal complete")
print(json.dumps({"type": "result", "is_error": False, "result": "ok", "total_cost_usd": 0.02,
                  "usage": {"input_tokens": 7, "output_tokens": 3}}))
'''


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "proj"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "E2E")
        git(self.repo, "config", "user.email", "e2e@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "f.txt").write_text("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "checkout", "-q", "-b", "dev")  # loop-check rejects base main
        home = self.tmp / "home"
        home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("NEXUS_API_KEY", "NEXUS_BASE_URL", "COUNCIL_LOOP_RUN")}
        self.env.update(HOME=str(home), USERPROFILE=str(home))

    def loop(self, *args):
        proc = subprocess.run([sys.executable, str(LOOP), *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def test_init_add_run_merges_task(self):
        self.loop("init")  # creates .council/config.json, so the edit below comes after it
        stub = self.tmp / "claude.py"  # outside the repo so the tree stays clean
        stub.write_text(f"#!{sys.executable}\n" + STUB)
        stub.chmod(0o755)
        bin_path = stub
        if os.name == "nt":
            bin_path = self.tmp / "claude.cmd"
            bin_path.write_text(f'@"{sys.executable}" "{stub}" %*\r\n')
        cfg_path = self.repo / ".council" / "config.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["loop"]["claude_bin"] = str(bin_path)
        cfg["loop"]["secrets"] = {"backend": "env"}
        cfg_path.write_text(json.dumps(cfg))

        self.loop("add", "Add e2e.txt", "--verify", f'"{sys.executable}" -c "pass"')
        self.loop("run")

        task = loop_tasks.TaskStore(self.repo / "tasks" / "tasks.json").load()[0]
        self.assertEqual(task["status"], "done", task)
        parents = git(self.repo, "rev-list", "--parents", "-n1", "dev").split()
        self.assertEqual(len(parents) - 1, 2, parents)
        self.assertEqual(git(self.repo, "show", "dev:e2e.txt"), "done")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

        count = git(self.repo, "rev-list", "--all", "--count")
        self.loop("run")  # nothing left to do: exit 0, no new commits
        self.assertEqual(git(self.repo, "rev-list", "--all", "--count"), count)


if __name__ == "__main__":
    unittest.main()
