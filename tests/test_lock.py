"""Multi-process tests for the OS-held lock and concurrent claims."""

import multiprocessing as mp
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_tasks import LockTimeout, TaskStore, os_lock, try_lock, unlock  # noqa: E402

CTX = mp.get_context("spawn")  # spawn behaves the same on Linux and Windows


def _claimer(path, owner, out):
    """Child: claim tasks until none are left, report the ids it got."""
    store = TaskStore(path, lock_timeout=30)
    got = []
    while True:
        task = store.claim(owner)
        if task is None:
            break
        got.append(task["id"])
    out.put((owner, got))


def _hold(lock_path, ready, seconds):
    """Child: take the lock, signal the parent, then keep holding it."""
    with os_lock(lock_path):
        ready.set()
        time.sleep(seconds)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.lock = self.dir / "x.lock"

    def tearDown(self):
        self.tmp.cleanup()

    def _start_holder(self, seconds=30):
        ready = CTX.Event()
        proc = CTX.Process(target=_hold, args=(str(self.lock), ready, seconds))
        proc.start()
        self.assertTrue(ready.wait(30), "child never took the lock")
        return proc

    def test_eight_processes_claim_twenty_tasks_once(self):
        path = self.dir / "tasks.json"
        store = TaskStore(path)
        for i in range(20):
            store.add(f"task {i}")
        out = CTX.Queue()
        procs = [CTX.Process(target=_claimer, args=(str(path), f"w{n}", out)) for n in range(8)]
        for p in procs:
            p.start()
        results = dict(out.get(timeout=120) for _ in procs)
        for p in procs:
            p.join(30)
            self.assertEqual(p.exitcode, 0)
        claimed = [tid for ids in results.values() for tid in ids]
        self.assertEqual(sorted(claimed), [f"t{i:03d}" for i in range(1, 21)])  # each exactly once
        # No lost writes: the file agrees with what every worker was handed.
        for t in store.load():
            self.assertEqual(t["status"], "claimed")
            self.assertIn(t["id"], results[t["owner"]])

    def test_killed_holder_releases_immediately(self):
        proc = self._start_holder()
        proc.kill()
        proc.join(10)
        start = time.monotonic()
        with os_lock(self.lock, timeout=5):
            pass
        self.assertLess(time.monotonic() - start, 1.0)

    def test_timeout_and_try_lock_while_held(self):
        proc = self._start_holder()
        try:
            self.assertIsNone(try_lock(self.lock))
            with self.assertRaises(LockTimeout) as cm:
                with os_lock(self.lock, timeout=0.3):
                    pass
            self.assertIn(str(self.lock), str(cm.exception))
        finally:
            proc.kill()
            proc.join(10)
        fh = try_lock(self.lock)
        self.assertIsNotNone(fh)
        unlock(fh)


if __name__ == "__main__":
    unittest.main()
