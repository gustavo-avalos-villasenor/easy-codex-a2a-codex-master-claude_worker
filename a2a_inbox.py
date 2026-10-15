"""Inbox for a worker in attached mode.

In attached mode the bridge does not start its own Claude session. The Claude
Code conversation that launched the bridge answers each task:

    python a2a_inbox.py wait                # blocks until a task arrives
    python a2a_inbox.py reply TASK-ID --file answer.md
    python a2a_inbox.py reply TASK-ID --failed "reason"

`wait` prints the pending task as JSON and exits, so the conversation can run
it as a background command and is notified when the master sends a message.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


ROOT = Path(__file__).resolve().parent


def default_task_db() -> Path:
    state_dir = Path(os.environ.get("A2A_STATE_DIR") or ROOT / "state")
    return state_dir.expanduser() / "tasks.sqlite3"


def check_session(path: Path, force: bool) -> None:
    """Refuse to serve a bridge mounted on a different conversation."""

    current = os.environ.get("CLAUDE_CODE_SESSION_ID")
    try:
        mounted = json.loads(
            (path.parent / "attached-session.json").read_text(encoding="utf-8")
        ).get("sessionId")
    except (OSError, ValueError, AttributeError):
        return
    if force or not current or not mounted or current == mounted:
        return
    raise SystemExit(
        f"This worker is mounted on Claude Code session {mounted}, not on this "
        f"conversation ({current}). Use --force to take its tasks anyway."
    )


def connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"Task database not found: {path}. Is the worker running?")
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def pending(connection: sqlite3.Connection) -> sqlite3.Row | None:
    try:
        return connection.execute(
            "SELECT task_id, prompt, created_at FROM attached_requests "
            "WHERE reply_state IS NULL ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        # The bridge creates the table on startup; treat a missing table as empty.
        return None


def wait(path: Path, timeout: float, poll: float) -> int:
    deadline = None if timeout <= 0 else time.monotonic() + timeout
    while True:
        row = None
        # The waiter may start before the user runs the listener.
        if path.is_file():
            with connect(path) as connection:
                row = pending(connection)
        if row is not None:
            print(
                json.dumps(
                    {
                        "taskId": row["task_id"],
                        "createdAt": row["created_at"],
                        "prompt": row["prompt"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print(
                f"\nReply with: python a2a_inbox.py reply {row['task_id']} --file ANSWER",
                file=sys.stderr,
            )
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            print("No task arrived before the timeout.", file=sys.stderr)
            return 2
        time.sleep(poll)


def reply(path: Path, task_id: str, text: str, failed: bool) -> int:
    if not text.strip():
        raise SystemExit("The reply is empty.")
    state = "TASK_STATE_FAILED" if failed else "TASK_STATE_COMPLETED"
    with connect(path) as connection:
        cursor = connection.execute(
            "UPDATE attached_requests SET reply_state = ?, reply_text = ?, replied_at = ? "
            "WHERE task_id = ? AND reply_state IS NULL",
            (state, text, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), task_id),
        )
    if cursor.rowcount != 1:
        raise SystemExit(f"Task {task_id} is not waiting for a reply.")
    print(f"Reply stored for {task_id} ({state}).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task-db", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)

    wait_parser = commands.add_parser("wait", help="Block until a task is pending.")
    wait_parser.add_argument("--timeout", type=float, default=0, help="Seconds; 0 waits forever.")
    wait_parser.add_argument("--poll", type=float, default=1.0)
    wait_parser.add_argument(
        "--force", action="store_true",
        help="Serve the worker even if it is mounted on another conversation.",
    )

    reply_parser = commands.add_parser("reply", help="Answer a pending task.")
    reply_parser.add_argument("task_id")
    reply_parser.add_argument("text", nargs="?", help="Answer text; defaults to stdin.")
    reply_parser.add_argument("--file", type=Path, help="Read the answer from a file.")
    reply_parser.add_argument("--failed", action="store_true", help="Report the task as failed.")

    args = parser.parse_args()
    path = (args.task_db or default_task_db()).expanduser().resolve()
    if args.command == "wait":
        check_session(path, args.force)
        raise SystemExit(wait(path, args.timeout, args.poll))
    if args.file:
        text = args.file.read_text(encoding="utf-8")
    elif args.text is not None:
        text = args.text
    else:
        text = sys.stdin.read()
    raise SystemExit(reply(path, args.task_id, text, args.failed))


if __name__ == "__main__":
    main()
