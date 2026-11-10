import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import loop_log  # noqa: E402

# Fake secrets, one per token type the redactor must catch.
FAKE_TOKENS = {
    "telegram": "123456789:AAHfakefakefakefakefakefakefake_1234",
    "telegram_url_part": "fakeBotPathSegment",
    "ghp": "ghp_" + "a" * 36,
    "gho": "gho_" + "B" * 36,
    "github_pat": "github_pat_" + "11ABCDEFG_" + "x" * 40,
    "sk": "sk-ant-api03-" + "Z" * 40,
    "bearer": "eyJhbGciOiJIUzI1NiJ9.fakepayload.fakesig",
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "pst": "pst_" + "q" * 30,
    "password": "hunter2hunter2",
    "token_kv": "plainTokenValue99",
    "secret_kv": "s3cr3tValue",
    "api_key_kv": "myApiKeyValue",
}

SECRET_TEXT = (
    f"bot {FAKE_TOKENS['telegram']} "
    f"url https://api.telegram.org/bot{FAKE_TOKENS['telegram_url_part']}/sendMessage "
    f"gh {FAKE_TOKENS['ghp']} {FAKE_TOKENS['gho']} {FAKE_TOKENS['github_pat']} "
    f"key {FAKE_TOKENS['sk']} "
    f"Authorization: Bearer {FAKE_TOKENS['bearer']} "
    f"aws {FAKE_TOKENS['aws']} pst {FAKE_TOKENS['pst']} "
    f"password={FAKE_TOKENS['password']} token={FAKE_TOKENS['token_kv']} "
    f"secret={FAKE_TOKENS['secret_kv']} api_key={FAKE_TOKENS['api_key_kv']}"
)


class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "logs"
        self.loggers = []

    def tearDown(self):
        for lg in self.loggers:
            lg.close()
        self.tmp.cleanup()

    def logger(self, **kw):
        lg = loop_log.get_logger(self.dir, **kw)
        self.loggers.append(lg)
        return lg

    def lines(self, name="loop.jsonl"):
        return (self.dir / name).read_text(encoding="utf-8").splitlines()

    def test_record_fields(self):
        self.logger().event("session_end", task_id="T4", agent="engineer", model="opus",
                            outcome="ok", run_id="r1", duration_s=1.5, cost_usd=0.02, tokens=100)
        rec = json.loads(self.lines()[0])
        for key in ("ts", "task_id", "agent", "model", "outcome", "event", "run_id"):
            self.assertIn(key, rec)
        self.assertTrue(rec["ts"].endswith("Z"))
        self.assertEqual((rec["task_id"], rec["agent"], rec["model"], rec["outcome"]),
                         ("T4", "engineer", "opus", "ok"))
        self.assertEqual((rec["duration_s"], rec["cost_usd"], rec["tokens"]), (1.5, 0.02, 100))

    def test_rotation(self):
        lg = self.logger(max_bytes=300, backups=2)
        for i in range(60):
            lg.event("tick", task_id="T4", n=i)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ["loop.jsonl", "loop.jsonl.1", "loop.jsonl.2"])

    def test_tokens_never_written(self):
        lg = self.logger()
        lg.event("leak", task_id="T4", outcome=SECRET_TEXT,
                 nested={"a": [SECRET_TEXT, {"b": SECRET_TEXT}]})
        loop_log.write_session_log(self.dir, "s1", SECRET_TEXT)
        jsonl = (self.dir / "loop.jsonl").read_text(encoding="utf-8")
        session = (self.dir / "sessions" / "s1.log").read_text(encoding="utf-8")
        json.loads(jsonl)  # redaction keeps the line valid JSON
        for name, value in FAKE_TOKENS.items():
            self.assertNotIn(value, jsonl, name)
            self.assertNotIn(value, session, name)
        self.assertIn("[REDACTED]", jsonl)

    def test_review_token_shapes(self):
        # Env-style, query-string, JSON-text, header and URL-credential secrets.
        cases = {
            "SERVICE_API_KEY=abc123def": "abc123def",
            "GITHUB_TOKEN=tok456value": "tok456value",
            "DB_PASSWORD=hunter2": "hunter2",
            "https://x.test/cb?access_token=abc123": "abc123",
            '{"token": "jsonsecret1"}': "jsonsecret1",
            "Authorization: Basic dXNlcjpwYXNz": "dXNlcjpwYXNz",
            "https://alice:urlpass99@host.test/repo": "urlpass99",
        }
        lg = self.logger()
        for text, value in cases.items():
            lg.event("leak", note=text)
            loop_log.write_session_log(self.dir, "shape", text)
            session = (self.dir / "sessions" / "shape.log").read_text(encoding="utf-8")
            self.assertNotIn(value, session, text)
        jsonl = (self.dir / "loop.jsonl").read_text(encoding="utf-8")
        for text, value in cases.items():
            self.assertNotIn(value, jsonl, text)

    def test_dict_key_redaction(self):
        out = loop_log.redact({"api_key": "plainvalue", "nested": [{"DB_PASSWORD": "pw1"}],
                               "tokens": 123, "input_tokens": 5, "note": "tokens used: 5"})
        self.assertEqual(out["api_key"], "[REDACTED]")
        self.assertEqual(out["nested"][0]["DB_PASSWORD"], "[REDACTED]")
        self.assertEqual((out["tokens"], out["input_tokens"], out["note"]), (123, 5, "tokens used: 5"))
        self.assertEqual(loop_log.redact({"tokens": 123, "input_tokens": 5}),
                         {"tokens": 123, "input_tokens": 5})

    def test_session_name_sanitised(self):
        path = loop_log.write_session_log(self.dir, "../evil/name", "x")
        self.assertEqual(path.parent, self.dir / "sessions")
        self.assertEqual(path.name, ".._evil_name.log")
        for bad in (".", "..", ""):
            with self.assertRaises(ValueError):
                loop_log.write_session_log(self.dir, bad, "x")

    def test_registered_secret(self):
        value = "Xy9-plain-looking-value"
        loop_log.register_secret(value)
        self.logger().event("fetch", note=f"got {value}")
        loop_log.write_session_log(self.dir, "s2", f"value={value} end")
        self.assertNotIn(value, (self.dir / "loop.jsonl").read_text(encoding="utf-8"))
        self.assertNotIn(value, (self.dir / "sessions" / "s2.log").read_text(encoding="utf-8"))

    def test_no_duplicate_lines(self):
        self.logger()
        self.logger().event("once")
        self.assertEqual(len(self.lines()), 1)

    def test_session_pruning(self):
        for i in range(5):
            path = loop_log.write_session_log(self.dir, f"s{i}", "x", keep=10)
            os.utime(path, (1000 + i, 1000 + i))  # deterministic ages
        loop_log.write_session_log(self.dir, "s5", "x", keep=3)
        names = sorted(p.name for p in (self.dir / "sessions").iterdir())
        self.assertEqual(names, ["s3.log", "s4.log", "s5.log"])

    def test_tail(self):
        lg = self.logger()
        for i in range(6):
            lg.event("e", task_id="A" if i % 2 else "B", n=i)
        self.loggers.pop().close()
        with open(self.dir / "loop.jsonl", "a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-01-01T00:00:00Z", "task_id": "A", "ev')  # truncated
        self.assertEqual([r["n"] for r in loop_log.tail(self.dir, n=2, task_id="A")], [3, 5])
        self.assertEqual(len(loop_log.tail(self.dir)), 6)
        self.assertEqual(loop_log.tail(Path(self.tmp.name) / "missing"), [])


if __name__ == "__main__":
    unittest.main()
