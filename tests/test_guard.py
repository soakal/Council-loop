"""Tests for scripts/hooks/guard.py, run as a subprocess exactly as Claude Code runs it."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "guard.py"


def run_guard(payload, strict=True, loop_run=False, cwd=None):
    """Feed a payload (dict or raw str) to guard.py; return True if it denied."""
    env = {k: v for k, v in os.environ.items() if k != "COUNCIL_LOOP_RUN"}
    if loop_run:
        env["COUNCIL_LOOP_RUN"] = "1"
    if isinstance(payload, dict):
        payload = json.dumps({"cwd": cwd, **payload})
    args = [sys.executable, str(GUARD)] + (["--strict"] if strict else [])
    r = subprocess.run(args, input=payload, capture_output=True, text=True, env=env, cwd=cwd)
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return False
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and out["permissionDecisionReason"]
    return True


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@localhost", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def bash(self, command, **kw):
        kw.setdefault("cwd", self.dir)
        return run_guard({"tool_name": "Bash", "tool_input": {"command": command}}, **kw)

    def tool(self, name, strict=True, loop_run=False, **tool_input):
        return run_guard({"tool_name": name, "tool_input": tool_input},
                         strict=strict, loop_run=loop_run, cwd=self.dir)

    def repo_on(self, branch):
        git(self.dir, "init", "-q")
        git(self.dir, "commit", "-q", "--allow-empty", "-m", "init")
        git(self.dir, "checkout", "-q", "-B", branch)

    # --- payload handling ---
    def test_unparseable_payload_denied(self):
        self.assertTrue(run_guard("not json {", cwd=self.dir))
        self.assertTrue(run_guard("not json {", strict=False, cwd=self.dir))
        self.assertTrue(run_guard("[]", cwd=self.dir))

    def test_unrelated_tool_allowed(self):
        self.assertFalse(self.tool("WebSearch", query="x"))

    # --- base rules (no --strict) ---
    def test_history_jsonl_edit_denied_in_base_mode(self):
        self.assertTrue(self.tool("Edit", strict=False, file_path="/p/.council/state/history.jsonl", new_string="x"))
        self.assertFalse(self.tool("Edit", strict=False, file_path="/p/.council/state/goal.md", new_string="x"))

    def test_secret_into_tracked_file(self):
        self.repo_on("feature")
        Path(self.dir, "tracked.md").write_text("x")
        git(self.dir, "add", "tracked.md")
        secret = "NEXUS_API_KEY=abc"
        self.assertTrue(self.tool("Write", strict=False, file_path="tracked.md", content=secret))
        self.assertTrue(self.tool("MultiEdit", strict=False, file_path="tracked.md",
                                  edits=[{"new_string": "http://box.tail1.ts.net"}]))
        self.assertFalse(self.tool("Write", strict=False, file_path="untracked.json", content=secret))
        self.assertFalse(self.tool("Write", strict=False, file_path="tracked.md", content="mentions NEXUS_API_KEY in prose"))

    def test_strict_rules_off_without_strict(self):
        self.assertFalse(self.bash("rm -rf x", strict=False))
        self.assertFalse(self.bash("cat .env", strict=False))
        self.assertFalse(self.tool("Read", strict=False, file_path=".env"))

    # --- rm -rf ---
    def test_rm_rf(self):
        for cmd in ["rm -rf x", "rm -fr x", "rm -r -f x", "rm -Rf x", "rm --recursive --force x",
                    "Remove-Item -Recurse -Force x", "rd /s /q x", "rmdir /S x", "sudo rm -rf /"]:
            self.assertTrue(self.bash(cmd), cmd)
        for cmd in ["rm file", "rm -r dir", "rm -f file", "Remove-Item x"]:
            self.assertFalse(self.bash(cmd), cmd)

    # --- force push ---
    def test_git_push_force(self):
        for cmd in ["git push --force", "git push -f origin feat", "git push --force-with-lease origin feat",
                    "git push --mirror origin", "git push origin +feat"]:
            self.assertTrue(self.bash(cmd), cmd)
        self.assertFalse(self.bash("git push origin revamp/console-loop"))

    # --- push to main ---
    def test_git_push_main(self):
        for cmd in ["git push origin main", "git push origin HEAD:main", "git push origin +main",
                    "git push origin feat:refs/heads/master", "git push --all origin"]:
            self.assertTrue(self.bash(cmd), cmd)
        self.assertFalse(self.bash("git push origin revamp/console-loop"))
        self.assertFalse(self.bash("git push -u origin feat"))

    def test_bare_push_on_main_denied(self):
        self.repo_on("main")
        self.assertTrue(self.bash("git push"))
        self.assertTrue(self.bash("git push origin"))
        self.assertTrue(self.bash("git push origin HEAD"))

    def test_bare_push_on_feature_allowed(self):
        self.repo_on("feature")
        self.assertFalse(self.bash("git push"))
        self.assertFalse(self.bash("git push origin"))

    def test_bare_push_outside_git_fails_closed(self):
        self.assertTrue(self.bash("git push"))

    # --- merge/rebase/cherry-pick/commit on main ---
    def test_git_write_on_main(self):
        self.repo_on("master")
        for cmd in ["git commit -m x", "git merge feat", "git rebase feat", "git cherry-pick abc"]:
            self.assertTrue(self.bash(cmd), cmd)
        self.assertFalse(self.bash("git status"))

    def test_git_write_on_feature_allowed(self):
        self.repo_on("feature")
        self.assertFalse(self.bash("git commit -m x"))
        self.assertFalse(self.bash("git merge main"))

    # --- disk tools ---
    def test_disk_tools(self):
        for cmd in ["mkfs.ext4 /dev/sdb1", "fdisk /dev/sda", "wipefs -a /dev/sda", "dd if=x of=/dev/sda",
                    "format D:", "Format-Volume -DriveLetter D", "diskpart"]:
            self.assertTrue(self.bash(cmd), cmd)
        self.assertFalse(self.bash("dd if=/dev/zero of=out.img bs=1M count=1"))
        self.assertFalse(self.bash("ls -la"))

    # --- credential files ---
    def test_cred_files_bash(self):
        for cmd in ["cat .env", "head -n 5 .env.local", "type .env", "Get-Content .env", "tail ~/.netrc",
                    "less id_rsa", "cat ~/.aws/credentials", "cat ~/.infisical/infisical-config.json", "wc -l < .env"]:
            self.assertTrue(self.bash(cmd), cmd)
        for cmd in ["cat .env.example", "cat .env.sample", "cat id_rsa.pub", "cat README.md"]:
            self.assertFalse(self.bash(cmd), cmd)

    def test_cred_files_file_tools(self):
        self.assertTrue(self.tool("Read", file_path=".env"))
        self.assertTrue(self.tool("Read", file_path="certs/server.pem"))
        self.assertTrue(self.tool("Read", file_path="/home/u/.ssh/id_ed25519"))
        self.assertTrue(self.tool("Edit", file_path=".git-credentials", new_string="x"))
        self.assertTrue(self.tool("NotebookEdit", notebook_path=".credentials.json", new_source="x"))
        self.assertTrue(self.tool("Grep", pattern="KEY", path=".env"))
        self.assertTrue(self.tool("Glob", pattern="**/.env"))
        self.assertFalse(self.tool("Read", file_path=".env.example"))
        self.assertFalse(self.tool("Read", file_path="/home/u/.ssh/id_ed25519.pub"))
        self.assertFalse(self.tool("Grep", pattern="KEY", path="src"))
        self.assertFalse(self.tool("Glob", pattern="**/*.py"))

    def test_read_symlink_to_env_denied(self):
        target = Path(self.dir, ".env")
        target.write_text("SECRET=1")
        link = Path(self.dir, "innocent.txt")
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self.assertTrue(self.tool("Read", file_path=str(link)))

    # --- infisical ---
    def test_infisical_writes(self):
        self.assertTrue(self.bash("infisical secrets set FOO=bar"))
        self.assertTrue(self.bash("infisical secrets delete FOO"))
        self.assertFalse(self.bash("infisical secrets get FOO"))

    # --- loop-state files (COUNCIL_LOOP_RUN=1 only) ---
    def test_loop_state_edits_in_loop_run(self):
        self.assertTrue(self.tool("Edit", loop_run=True, file_path="tasks/tasks.json", new_string="x"))
        self.assertTrue(self.tool("Write", loop_run=True, file_path=".council/config.local.json", content="{}"))
        for cmd in ["echo x > tasks/tasks.json", "sed -i s/a/b/ .council/config.json",
                    "cp x .council/state/run-journal.json", "mv tasks/tasks.json /tmp/t"]:
            self.assertTrue(self.bash(cmd, loop_run=True), cmd)
        self.assertFalse(self.bash("cat tasks/tasks.json", loop_run=True))
        self.assertFalse(self.tool("Read", loop_run=True, file_path="tasks/tasks.json"))
        self.assertFalse(self.tool("Edit", loop_run=True, file_path=".council/config.example.json", new_string="x"))

    def test_loop_state_edits_allowed_with_strict_only(self):
        self.assertFalse(self.tool("Edit", file_path="tasks/tasks.json", new_string="x"))
        self.assertFalse(self.bash("echo x > tasks/tasks.json"))

    def test_env_var_alone_enables_strict(self):
        self.assertTrue(self.bash("rm -rf x", strict=False, loop_run=True))

    # --- command chains and wrappers ---
    def test_chains_and_wrappers(self):
        for cmd in ["echo hi && rm -rf x", "true; git push --force", "ls | cat .env", "false || mkfs.ext4 /dev/x",
                    "echo a\nrm -rf x", "bash -c 'rm -rf x'", "echo 'unbalanced && rm -rf x"]:
            self.assertTrue(self.bash(cmd), cmd)
        self.assertFalse(self.bash('echo "rm -rf x; git push --force"'))


if __name__ == "__main__":
    unittest.main()
