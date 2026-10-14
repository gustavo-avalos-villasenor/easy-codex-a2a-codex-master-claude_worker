from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from a2a_bridge_claude import ClaudeWorker  # noqa: E402


SESSION_ID = "22222222-2222-4222-8222-222222222222"


class ClaudeWorkerSessionTest(unittest.TestCase):
    def test_creates_and_reuses_persistent_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="claude-worker-test-") as temporary:
            temp_dir = Path(temporary)
            fake_claude = temp_dir / "claude"
            calls = temp_dir / "calls.jsonl"
            fake_claude.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if '--session-id' in args:\n"
                "    session = '22222222-2222-4222-8222-222222222222'\n"
                "elif '--resume' in args:\n"
                "    session = args[args.index('--resume') + 1]\n"
                "else:\n"
                "    raise SystemExit('missing session flag')\n"
                "prompt = args[-1]\n"
                "log = pathlib.Path(" + repr(str(calls)) + ")\n"
                "with log.open('a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps({'session': session, 'prompt': prompt}) + '\\n')\n"
                "print(json.dumps({'subtype': 'success', 'is_error': False, "
                "'result': 'response to ' + prompt, 'session_id': session}))\n",
                encoding="utf-8",
            )
            fake_claude.chmod(
                fake_claude.stat().st_mode
                | stat.S_IXUSR
                | stat.S_IXGRP
                | stat.S_IXOTH
            )

            session_file = temp_dir / "state" / "claude-session.json"
            environment = {
                "A2A_CLAUDE_CLI": str(fake_claude),
            }
            previous = {
                key: os.environ.get(key)
                for key in environment
            }
            os.environ.update(environment)
            try:
                worker = ClaudeWorker(
                    cwd=temp_dir,
                    session_file=session_file,
                    timeout=10,
                    model=None,
                    effort=None,
                    permission_mode="acceptEdits",
                    tools=None,
                    system_prompt=None,
                )
                self.assertEqual(worker.ask("one"), "response to one")
                self.assertEqual(
                    json.loads(session_file.read_text(encoding="utf-8"))["sessionId"],
                    SESSION_ID,
                    "the fake CLI should return a stable session id",
                )
                second_worker = ClaudeWorker(
                    cwd=temp_dir,
                    session_file=session_file,
                    timeout=10,
                    model=None,
                    effort=None,
                    permission_mode="acceptEdits",
                    tools=None,
                    system_prompt=None,
                )
                self.assertEqual(second_worker.ask("two"), "response to two")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            recorded = [
                json.loads(line)
                for line in calls.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                recorded,
                [
                    {"session": recorded[0]["session"], "prompt": "one"},
                    {"session": SESSION_ID, "prompt": "two"},
                ],
            )
            self.assertEqual(recorded[0]["session"], SESSION_ID)


if __name__ == "__main__":
    unittest.main()
