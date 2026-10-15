from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import a2a_bridge_claude  # noqa: E402
from a2a_bridge_claude import TaskStore, resolve_executor  # noqa: E402


SESSION_ID = "33333333-3333-4333-8333-333333333333"


class AttachedWorkerTest(unittest.TestCase):
    def test_resolve_executor(self) -> None:
        previous = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            self.assertEqual(resolve_executor("auto"), ("headless", None))
            with self.assertRaises(RuntimeError):
                resolve_executor("attached")
            os.environ["CLAUDE_CODE_SESSION_ID"] = SESSION_ID
            self.assertEqual(resolve_executor("auto"), ("attached", SESSION_ID))
            self.assertEqual(resolve_executor("headless"), ("headless", None))
        finally:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            if previous is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = previous

    def test_inbox_wait_and_reply_complete_the_task(self) -> None:
        a2a_bridge_claude.ATTACHED_POLL_SECONDS = 0.05
        with tempfile.TemporaryDirectory(prefix="attached-worker-test-") as temporary:
            task_db = Path(temporary) / "tasks.sqlite3"
            store = TaskStore(task_db, "claude-worker", None, False, attached=True)
            try:
                record = store.create("hola worker")
                self.assertTrue(record.working.wait(5))

                inbox = [sys.executable, str(ROOT / "a2a_inbox.py"), "--task-db", str(task_db)]
                waited = subprocess.run(
                    inbox + ["wait", "--timeout", "5", "--poll", "0.05"],
                    capture_output=True, text=True, check=True,
                )
                self.assertIn(record.task_id, waited.stdout)
                self.assertIn("hola worker", waited.stdout)

                subprocess.run(
                    inbox + ["reply", record.task_id, "respuesta"],
                    capture_output=True, text=True, check=True,
                )
                self.assertTrue(record.done.wait(5))
                self.assertEqual(record.state, "TASK_STATE_COMPLETED")
                self.assertEqual(record.text, "respuesta")

                again = subprocess.run(
                    inbox + ["wait", "--timeout", "0.2", "--poll", "0.05"],
                    capture_output=True, text=True,
                )
                self.assertEqual(again.returncode, 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
