"""Unit tests for the loop task store (scripts/loop_tasks.py)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_tasks import TaskStore, TaskStoreError  # noqa: E402


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = TaskStore(self.dir / "tasks" / "tasks.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_empty(self):
        self.assertEqual(self.store.load(), [])
        self.assertEqual(self.store.summary()["pending"], 0)

    def test_add_claim_update_release_notes(self):
        t = self.store.add("first", acceptance="works", verify=["true"])
        self.assertEqual(t["id"], "t001")
        self.assertEqual(self.store.add("second")["id"], "t002")
        data = json.loads(self.store.path.read_text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["tasks"][0]["verify"], ["true"])

        got = self.store.claim("w1")
        self.assertEqual((got["id"], got["status"], got["owner"]), ("t001", "claimed", "w1"))
        self.assertIsNotNone(got["claimed_at"])

        self.store.update("t001", attempts=2)
        back = self.store.release("t001")
        self.assertEqual((back["status"], back["owner"], back["attempts"]), ("pending", None, 2))
        with self.assertRaises(TaskStoreError):
            self.store.release("t001")  # not claimed any more

        self.store.add_note("t001", "stash abc123")
        self.assertEqual(self.store.load()[0]["notes"][0]["text"], "stash abc123")

        self.store.claim("w1")
        self.store.update("t001", status="done", commit="deadbeef")
        self.assertEqual(self.store.summary()["done"], 1)

    def test_duplicate_explicit_id_rejected(self):
        self.store.add("a", task_id="x1")
        with self.assertRaises(TaskStoreError):
            self.store.add("a again", task_id="x1")
        self.assertEqual(len(self.store.load()), 1)

    def test_unknown_and_circular_dependency_rejected(self):
        self.store.add("a")
        with self.assertRaises(TaskStoreError):
            self.store.add("b", depends_on=["nope"])
        with self.assertRaises(TaskStoreError):
            self.store.add("self", task_id="s", depends_on=["s"])
        self.store.add("b", depends_on=["t001"])
        with self.assertRaises(TaskStoreError):
            self.store.update("t001", depends_on=["t002"])  # t001 <-> t002
        self.assertEqual(self.store.load()[0]["depends_on"], [])  # nothing written
        self.assertEqual(len(self.store.load()), 2)

    def test_claim_respects_dependencies(self):
        self.store.add("a")
        self.store.add("b", depends_on=["t001"])
        self.assertEqual(self.store.claim("w")["id"], "t001")
        self.assertIsNone(self.store.claim("w"))  # t002 waits for t001
        self.store.update("t001", status="done")
        self.assertEqual(self.store.claim("w")["id"], "t002")
        self.assertIsNone(self.store.claim("w"))

    def test_claim_uses_numeric_id_order(self):
        self.store.add("later", task_id="t1000")
        self.store.add("earlier", task_id="t999")
        self.assertEqual(self.store.claim("w")["id"], "t999")

    def test_task_missing_id_or_status_rejected(self):
        self.store.path.parent.mkdir(parents=True)
        for bad in ({"status": "pending"}, {"id": "t001"}, {"id": "t001", "status": "weird"}):
            self.store.path.write_text(json.dumps({"version": 1, "tasks": [bad]}))
            with self.assertRaisesRegex(TaskStoreError, "task #0"):
                self.store.load()

    def test_block_unreachable_transitive(self):
        self.store.add("a")
        self.store.add("b", depends_on=["t001"])
        self.store.add("c", depends_on=["t002"])
        self.store.add("d")
        self.store.claim("w")
        self.store.update("t001", status="failed", reason="3 fails")
        self.assertEqual(sorted(self.store.block_unreachable()), ["t002", "t003"])
        tasks = {t["id"]: t for t in self.store.load()}
        self.assertEqual(tasks["t002"]["reason"], "dependency t001")
        self.assertEqual(tasks["t003"]["reason"], "dependency t002")
        self.assertEqual(tasks["t004"]["status"], "pending")
        self.assertEqual(self.store.block_unreachable(), [])

    def test_invalid_transitions_rejected(self):
        self.store.add("a")
        with self.assertRaises(TaskStoreError):
            self.store.update("t001", status="done")  # pending -> done
        with self.assertRaises(TaskStoreError):
            self.store.update("t001", status="bogus")
        with self.assertRaises(TaskStoreError):
            self.store.update("t001", id="zzz")  # id is not updatable
        self.store.claim("w")
        self.store.update("t001", status="done")
        with self.assertRaises(TaskStoreError):
            self.store.update("t001", status="pending")  # done is final
        with self.assertRaises(TaskStoreError):
            self.store.update("nope", attempts=1)

    def test_corrupt_file_raises_and_is_kept(self):
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text("{not json")
        with self.assertRaises(TaskStoreError):
            self.store.load()
        with self.assertRaises(TaskStoreError):
            self.store.add("a")
        self.assertEqual(self.store.path.read_text(), "{not json")

    def test_atomic_write_leaves_no_temp_files(self):
        for i in range(5):
            self.store.add(f"task {i}")
        names = sorted(p.name for p in self.store.path.parent.iterdir())
        self.assertEqual(names, ["tasks.json", "tasks.lock"])


if __name__ == "__main__":
    unittest.main()
