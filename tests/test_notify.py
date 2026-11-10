"""Tests for T9: secrets backends + NEXUS escalation (scripts/loop_notify.py, Runner.escalate).

A local fake NEXUS (http.server on 127.0.0.1, random port) and a fake secret command stand in
for the real ones; nothing here contacts a real NEXUS or a real secret store.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import loop  # noqa: E402
import loop_log  # noqa: E402
import loop_notify  # noqa: E402
import test_runner as tr  # noqa: E402  (module only: its TestCase is not re-collected here)

TOKEN = "nexus-test-" + "Zq7Xw2Lp9Rt4Vb8N"
# Prints TOKEN without the value ever being in argv (built from two halves at run time).
GOOD_CMD = ["{python}", "-c", "import sys; sys.stdout.write('nexus-test-' + 'Zq7Xw2Lp9Rt4Vb8N')", "{name}"]
MISSING_CMD = ["{python}", "-c", "import sys; sys.exit(1)", "{name}"]


class FakeNexus:
    """Records every POST; answers with `status`."""

    def __init__(self, status=201):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                       "json": json.loads(body)})
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id": 1}')

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def dead_url():
    """A 127.0.0.1 URL nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


class SecretsTests(unittest.TestCase):
    def test_command_backend_substitutes_and_registers(self):
        cfg = {"backend": "command",
               "command": ["{python}", "-c", "import sys; sys.stdout.write(sys.argv[1] + '-v4lue')", "{name}"]}
        self.assertEqual(loop_notify.get_secret("ABC_KEY", cfg), "ABC_KEY-v4lue")
        self.assertNotIn("ABC_KEY-v4lue", loop_log.redact("x ABC_KEY-v4lue y"))

    def test_command_backend_failure_and_empty(self):
        with self.assertRaises(loop_notify.NotifyError):
            loop_notify.get_secret("X", {"backend": "command", "command": MISSING_CMD})
        with self.assertRaises(loop_notify.NotifyError):
            loop_notify.get_secret("X", {"backend": "command", "command": ["no-such-secret-bin-xyz", "{name}"]})

    def test_env_backend(self):
        with mock.patch.dict(os.environ, {"T9_ENV_SECRET": "envval123"}):
            self.assertEqual(loop_notify.get_secret("T9_ENV_SECRET", {"backend": "env"}), "envval123")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("T9_ENV_SECRET", None)
            with self.assertRaises(loop_notify.NotifyError):
                loop_notify.get_secret("T9_ENV_SECRET", {"backend": "env"})

    def test_infisical_backend_is_read_only_get(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "fake_infisical.py"
            script.write_text("import json, sys\n"
                              f"open({str(Path(d) / 'argv.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
                              "sys.stdout.write('infval123\\n')\n")
            if os.name == "nt":
                (Path(d) / "infisical.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n')
            else:
                exe = Path(d) / "infisical"
                exe.write_text(f"#!{sys.executable}\n" + script.read_text())
                exe.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ.get("PATH", "")}):
                self.assertEqual(loop_notify.get_secret("NEXUS_API_KEY", {"backend": "infisical"}), "infval123")
            self.assertEqual(json.loads((Path(d) / "argv.json").read_text()),
                             ["secrets", "get", "NEXUS_API_KEY", "--plain"])

    def test_nexus_url_default_order(self):
        with mock.patch.dict(os.environ, {"NEXUS_BASE_URL": "http://env-host:1/"}):
            self.assertEqual(loop_notify.nexus_url({"nexus_url": ""}), "http://env-host:1")
            self.assertEqual(loop_notify.nexus_url({"nexus_url": "http://cfg:2"}), "http://cfg:2")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NEXUS_BASE_URL", None)
            self.assertEqual(loop_notify.nexus_url({"nexus_url": ""}), "http://127.0.0.1:8000")

    def test_disabled_sends_nothing(self):
        self.assertFalse(loop_notify.send_escalation(
            {"notify": {"enabled": False}, "secrets": {"backend": "command", "command": MISSING_CMD}},
            "blocked", "s", "d"))


class EscalationRunTests(unittest.TestCase):
    """Full `loop run` with the test_runner harness, notify pointed at a fake NEXUS."""

    setUp = tr.RunnerTests.setUp
    tearDown = tr.RunnerTests.tearDown
    scenario = tr.RunnerTests.scenario
    runner = tr.RunnerTests.runner
    task = tr.RunnerTests.task

    def configure(self, url, command):
        path = self.repo / ".council" / "config.json"
        cfg = json.loads(path.read_text())
        cfg["loop"]["notify"] = {"enabled": True, "nexus_url": url, "api_key_secret": "NEXUS_API_KEY"}
        cfg["loop"]["secrets"] = {"backend": "command", "command": command}
        path.write_text(json.dumps(cfg))

    def blocked_run(self):
        self.store.add("Needs creds", verify=[tr.PASS])
        self.scenario({"stop": "blocked: needs an API key"})
        r = self.runner()
        res = r.run()
        return r, res

    def logs_text(self):
        logs = self.repo / "logs"
        return "".join(p.read_text(encoding="utf-8", errors="replace") for p in logs.rglob("*") if p.is_file())

    def test_one_post_per_event_with_bearer_and_page_now(self):
        nexus = FakeNexus()
        self.addCleanup(nexus.close)
        self.configure(nexus.url, GOOD_CMD)
        r, res = self.blocked_run()
        self.assertEqual(res["exit_code"], 0)
        r.escalate("blocked", self.task(), "again")  # same task + event: deduped, no second POST
        self.assertEqual(len(nexus.requests), 1)
        req = nexus.requests[0]
        self.assertEqual(req["path"], "/api/safety/flags")
        self.assertEqual(req["auth"], f"Bearer {TOKEN}")
        body = req["json"]
        self.assertEqual((body["source"], body["check"], body["severity"], body["page_now"]),
                         ("council_loop", "blocked", "high", True))
        self.assertIn("t001", body["summary"])
        self.assertNotIn("\n", body["summary"])
        self.assertIn("needs an API key", body["detail"])
        text = self.logs_text()  # loop.jsonl + escalations.md + sessions/*.log
        self.assertIn("[blocked] task t001", text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("[undelivered]", text)
        self.assertFalse(loop._status(self.repo)["escalation_undelivered"])

    def assert_undelivered(self, res):
        self.assertEqual(res["exit_code"], 0)  # run finished normally
        self.assertEqual(self.task()["status"], "blocked")
        esc = (self.repo / "logs" / "escalations.md").read_text()
        self.assertEqual(esc.count("[blocked] task t001"), 1)
        self.assertIn("[undelivered] task t001", esc)
        self.assertTrue(loop._status(self.repo)["escalation_undelivered"])
        self.assertNotIn(TOKEN, self.logs_text())

    def test_missing_key_writes_file_and_continues(self):
        nexus = FakeNexus()
        self.addCleanup(nexus.close)
        self.configure(nexus.url, MISSING_CMD)
        _, res = self.blocked_run()
        self.assertEqual(nexus.requests, [])
        self.assert_undelivered(res)

    def test_server_down_writes_file_and_continues(self):
        self.configure(dead_url(), GOOD_CMD)
        _, res = self.blocked_run()
        self.assert_undelivered(res)

    def test_non_2xx_is_undelivered(self):
        nexus = FakeNexus(status=401)
        self.addCleanup(nexus.close)
        self.configure(nexus.url, GOOD_CMD)
        _, res = self.blocked_run()
        self.assertEqual(len(nexus.requests), 1)
        self.assert_undelivered(res)


if __name__ == "__main__":
    unittest.main()
