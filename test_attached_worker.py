from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import a2a_bridge_claude  # noqa: E402
from a2a_bridge_claude import TaskStore, resolve_executor  # noqa: E402
from a2a_inbox import HEARTBEAT_FILE, waiter_is_listening  # noqa: E402
from connection_pack import build_connection_pack, format_connection_pack  # noqa: E402


SESSION_ID = "33333333-3333-4333-8333-333333333333"


class AttachedWorkerTest(unittest.TestCase):
    def test_resolve_executor(self) -> None:
        previous = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            self.assertEqual(resolve_executor("auto"), ("headless", None))
            self.assertEqual(
                resolve_executor("auto", SESSION_ID), ("attached", SESSION_ID)
            )
            with self.assertRaises(RuntimeError):
                resolve_executor("auto", "not-a-uuid")
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

    def test_unreceived_task_fails_instead_of_hanging(self) -> None:
        a2a_bridge_claude.ATTACHED_POLL_SECONDS = 0.05
        previous = a2a_bridge_claude.ATTACHED_CLAIM_SECONDS
        a2a_bridge_claude.ATTACHED_CLAIM_SECONDS = 0.3
        with tempfile.TemporaryDirectory(prefix="attached-worker-test-") as temporary:
            task_db = Path(temporary) / "tasks.sqlite3"
            store = TaskStore(task_db, "claude-worker", None, False, attached=True)
            try:
                self.assertEqual(store.attached_status(), "not listening")
                record = store.create("nobody is listening")
                self.assertTrue(record.done.wait(5))
                self.assertEqual(record.state, "TASK_STATE_FAILED")
                self.assertIn("is not listening", record.text)
                self.assertIn("NOT executed", record.text)

                # A waiter started later must not receive the expired task.
                late = subprocess.run(
                    self._inbox(task_db) + ["wait", "--timeout", "0.2", "--poll", "0.05"],
                    capture_output=True, text=True,
                )
                self.assertEqual(late.returncode, 2)
            finally:
                store.close()
                a2a_bridge_claude.ATTACHED_CLAIM_SECONDS = previous

    def test_received_task_reports_progress_and_never_expires(self) -> None:
        a2a_bridge_claude.ATTACHED_POLL_SECONDS = 0.05
        previous = a2a_bridge_claude.ATTACHED_CLAIM_SECONDS
        a2a_bridge_claude.ATTACHED_CLAIM_SECONDS = 0.3
        with tempfile.TemporaryDirectory(prefix="attached-worker-test-") as temporary:
            task_db = Path(temporary) / "tasks.sqlite3"
            store = TaskStore(task_db, "claude-worker", None, False, attached=True)
            try:
                record = store.create("long task")
                subprocess.run(
                    self._inbox(task_db) + ["wait", "--timeout", "5", "--poll", "0.05"],
                    capture_output=True, text=True, check=True,
                )
                deadline = time.monotonic() + 5
                while "Received" not in record.text and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(record.state, "TASK_STATE_WORKING")
                self.assertIn("Received by the worker", record.text)
                self.assertEqual(store.attached_status(), "busy")

                time.sleep(0.6)  # longer than the claim timeout
                self.assertFalse(record.done.is_set())

                subprocess.run(
                    self._inbox(task_db) + ["reply", record.task_id, "done"],
                    capture_output=True, text=True, check=True,
                )
                self.assertTrue(record.done.wait(5))
                self.assertEqual(record.state, "TASK_STATE_COMPLETED")
            finally:
                store.close()
                a2a_bridge_claude.ATTACHED_CLAIM_SECONDS = previous

    def test_waiter_heartbeat_marks_the_worker_listening(self) -> None:
        with tempfile.TemporaryDirectory(prefix="attached-worker-test-") as temporary:
            state = Path(temporary)
            task_db = state / "tasks.sqlite3"
            store = TaskStore(task_db, "claude-worker", None, False, attached=True)
            waiter = subprocess.Popen(
                self._inbox(task_db) + ["wait", "--poll", "0.05"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not waiter_is_listening(state) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(store.attached_status(), "listening")
            finally:
                waiter.terminate()
                waiter.communicate(timeout=5)
                store.close()
            stale = json.loads((state / HEARTBEAT_FILE).read_text(encoding="utf-8"))
            stale["updatedAt"] = time.time() - 60
            (state / HEARTBEAT_FILE).write_text(json.dumps(stale), encoding="utf-8")
            self.assertFalse(waiter_is_listening(state))

    def test_reply_failed_accepts_the_reason_in_any_position(self) -> None:
        a2a_bridge_claude.ATTACHED_POLL_SECONDS = 0.05
        with tempfile.TemporaryDirectory(prefix="attached-worker-test-") as temporary:
            task_db = Path(temporary) / "tasks.sqlite3"
            store = TaskStore(task_db, "claude-worker", None, False, attached=True)
            try:
                for arguments in (["--failed", "reason one"], ["reason two", "--failed"]):
                    record = store.create("decline me")
                    self.assertTrue(record.working.wait(5))
                    subprocess.run(
                        self._inbox(task_db) + ["reply", record.task_id, *arguments],
                        capture_output=True, text=True, check=True,
                    )
                    self.assertTrue(record.done.wait(5))
                    self.assertEqual(record.state, "TASK_STATE_FAILED")
                    self.assertIn(record.text, ("reason one", "reason two"))
            finally:
                store.close()

    def test_old_database_gains_the_claimed_at_column(self) -> None:
        with tempfile.TemporaryDirectory(prefix="attached-worker-test-") as temporary:
            task_db = Path(temporary) / "tasks.sqlite3"
            with sqlite3.connect(task_db) as connection:
                connection.execute(
                    "CREATE TABLE attached_requests (task_id TEXT PRIMARY KEY, "
                    "prompt TEXT NOT NULL, created_at TEXT NOT NULL, reply_state TEXT, "
                    "reply_text TEXT, replied_at TEXT)"
                )
            TaskStore(task_db, "claude-worker", None, False, attached=True).close()
            with sqlite3.connect(task_db) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(attached_requests)")}
            self.assertIn("claimed_at", columns)

    def test_connection_pack_explains_attached_states(self) -> None:
        pack = build_connection_pack(
            base_url="http://100.64.0.1:8766",
            context_id="claude-worker",
            repository_url="https://github.com/owner/repo",
            executor="attached",
            session_id=SESSION_ID,
            claim_timeout="120",
        )
        pack["savedTo"] = "/srv/worker/state/connection-pack.txt"
        text = format_connection_pack(pack)
        self.assertIn("metadata.attachedWorker", text)
        self.assertIn("Received by the worker Claude Code conversation", text)
        self.assertIn("within 120 seconds", text)
        self.assertIn("is not listening", text)
        self.assertEqual(text.count("/srv/worker/state/connection-pack.txt"), 2)

    @staticmethod
    def _inbox(task_db: Path) -> list[str]:
        return [sys.executable, str(ROOT / "a2a_inbox.py"), "--task-db", str(task_db)]

    def test_inbox_refuses_another_conversation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="attached-worker-test-") as temporary:
            state = Path(temporary)
            (state / "attached-session.json").write_text(
                '{"sessionId": "%s"}' % SESSION_ID, encoding="utf-8"
            )
            inbox = [
                sys.executable, str(ROOT / "a2a_inbox.py"),
                "--task-db", str(state / "tasks.sqlite3"),
                "wait", "--timeout", "0.1", "--poll", "0.05",
            ]
            other = dict(os.environ, CLAUDE_CODE_SESSION_ID="44444444-4444-4444-8444-444444444444")
            refused = subprocess.run(inbox, capture_output=True, text=True, env=other)
            self.assertEqual(refused.returncode, 1)
            self.assertIn(SESSION_ID, refused.stderr)
            same = dict(os.environ, CLAUDE_CODE_SESSION_ID=SESSION_ID)
            waited = subprocess.run(inbox, capture_output=True, text=True, env=same)
            self.assertEqual(waited.returncode, 2, "no task yet, so it times out")


if __name__ == "__main__":
    unittest.main()
