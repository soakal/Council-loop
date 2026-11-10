"""T11 tests: `loop status` post-mortem line, council_doctor launcher + secret checks."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LOOP = SCRIPTS / "loop.py"
DOCTOR = SCRIPTS / "council_doctor.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class T11Tests(unittest.TestCase):
    def setUp(self):
        # Temp git repo on a dev branch, initialised for loop mode; no NEXUS key anywhere.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "proj"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "f.txt").write_text("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "switch", "-q", "-c", "dev")
        home = Path(tmp.name) / "home"
        home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("NEXUS_API_KEY", "NEXUS_BASE_URL", "COUNCIL_LOOP_RUN")}
        self.env.update(HOME=str(home), USERPROFILE=str(home))
        self.run_py(LOOP, "init")

    def run_py(self, script, *args, env=None):
        proc = subprocess.run([sys.executable, str(script), *args], cwd=self.repo, env=env or self.env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def log(self, *records):
        # Append raw loop.jsonl records, as the runner's event log would hold them.
        path = self.repo / "logs" / "loop.jsonl"
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    # (2) loop status shows the LAST post-mortem result
    def test_status_shows_last_postmortem(self):
        self.assertNotIn("post-mortem", self.run_py(LOOP, "status"))
        self.log({"event": "postmortem", "outcome": "ran", "reason": "council post-mortem: ok=False findings=1"},
                 {"event": "postmortem", "outcome": "ran", "reason": "council post-mortem: ok=True findings=0"},
                 {"event": "session", "outcome": "ok"})
        out = self.run_py(LOOP, "status")
        self.assertIn("last post-mortem: ran council post-mortem: ok=True findings=0", out)
        self.assertNotIn("findings=1", out)
        self.assertNotIn("rejected the API key", out)
        self.assertEqual(json.loads(self.run_py(LOOP, "status", "--json"))["postmortem"]["reason"],
                         "council post-mortem: ok=True findings=0")

    # (2) 401 / 403 from NEXUS is called out as a rejected key
    def test_status_highlights_rejected_key(self):
        for code in (401, 403):
            self.log({"event": "postmortem", "outcome": "ran",
                      "reason": f"council post-mortem skipped: request failed: HTTP Error {code}: Unauthorized"})
            self.assertIn("NEXUS rejected the API key", self.run_py(LOOP, "status"))
        self.log({"event": "postmortem", "outcome": "ran",
                  "reason": "council post-mortem skipped: request failed: HTTP Error 500: boom"})
        out = self.run_py(LOOP, "status")
        self.assertNotIn("rejected the API key", out)
        self.assertIn("HTTP Error 500", out)

    # (1) doctor: python launcher found; NEXUS_API_KEY via the env backend (never the real `secret` CLI)
    def test_doctor_launcher_and_secret_check(self):
        (self.repo / ".council" / "config.local.json").write_text(json.dumps({"loop": {"secrets": {"backend": "env"}}}))
        doctor = [sys.executable, str(DOCTOR), "--root", str(self.repo)]
        out = subprocess.run(doctor, cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120).stdout
        self.assertRegex(out, r"OK.*python launcher")
        self.assertRegex(out, r"WARN.*NEXUS_API_KEY secret.*not set \(env backend\)")
        env = dict(self.env, NEXUS_API_KEY="t11-sentinel-value")
        out = subprocess.run(doctor, cwd=self.repo, env=env, capture_output=True, text=True, timeout=120).stdout
        self.assertRegex(out, r"OK.*NEXUS_API_KEY secret.*fetched via env backend")
        self.assertNotIn("t11-sentinel-value", out)


if __name__ == "__main__":
    unittest.main()
