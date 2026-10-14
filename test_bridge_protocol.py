from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from a2a_bridge_claude import TaskStore, agent_card, message_prompt  # noqa: E402
from connection_pack import build_connection_pack, format_connection_pack  # noqa: E402


class BridgeProtocolTest(unittest.TestCase):
    def test_demo_task_is_processed_and_reloaded_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a2a-bridge-test-") as temporary:
            database = Path(temporary) / "tasks.sqlite3"
            store = TaskStore(database, "claude-worker", None, demo=True)
            try:
                record = store.create("test message")
                self.assertTrue(record.done.wait(5))
                self.assertEqual(record.state, "TASK_STATE_COMPLETED")
                self.assertEqual(record.text, "Demo Claude A2A: test message")
            finally:
                store.close()

            reopened = TaskStore(database, "claude-worker", None, demo=True)
            try:
                restored = reopened.get(record.task_id)
                self.assertIsNotNone(restored)
                assert restored is not None
                self.assertTrue(restored.done.is_set())
                self.assertEqual(restored.as_task()["status"]["state"], "TASK_STATE_COMPLETED")
            finally:
                reopened.close()

    def test_agent_card_and_text_parts_are_client_compatible(self) -> None:
        card = agent_card(
            "https://worker.tailnet.example:8766",
            "claude-worker",
            False,
            repository_url="https://github.com/owner/repo",
        )
        self.assertIn("contextId: claude-worker", card["description"])
        self.assertIn("A2A 1.0 over HTTP", card["description"])
        self.assertTrue(card["capabilities"]["streaming"])
        self.assertEqual(card["metadata"]["contextId"], "claude-worker")
        self.assertEqual(
            card["metadata"]["repositoryUrl"],
            "https://github.com/owner/repo",
        )
        self.assertEqual(
            message_prompt(
                {
                    "parts": [
                        {"text": "one"},
                        {"text": "two"},
                    ]
                }
            ),
            "one\ntwo",
        )

    def test_connection_pack_contains_remote_coordinates(self) -> None:
        pack = build_connection_pack(
            base_url="https://worker.tailnet.example:8766",
            context_id="claude-worker-prod",
            repository_url="https://github.com/owner/repo",
            listen_host="100.64.10.20",
            port=8766,
            session_id="session-42",
            mode="standby",
        )
        rendered = format_connection_pack(pack)
        self.assertIn("https://worker.tailnet.example:8766/.well-known/agent-card.json", rendered)
        self.assertIn("A2A context ID:  claude-worker-prod", rendered)
        self.assertIn("Do not assume that it is on localhost.", rendered)
        self.assertIn("https://github.com/owner/repo", rendered)


if __name__ == "__main__":
    unittest.main()
