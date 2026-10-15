"""Inbox for a worker in attached mode.

In attached mode the bridge does not start its own Claude session. The Claude
Code conversation that mounted the worker answers each task:

    python a2a_inbox.py wait                        # blocks until a task arrives
    python a2a_inbox.py reply TASK-ID --file answer.md
    python a2a_inbox.py reply TASK-ID --failed "reason"
    python a2a_inbox.py status                      # listener, waiter, pending tasks

`wait` prints the pending task as JSON and exits, so the conversation can run
it as a background command and is notified when the master sends a message.
While it waits it refreshes a heartbeat file; the bridge reads it to tell the
master whether the worker conversation is listening.
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
HEARTBEAT_FILE = "inbox-heartbeat.json"
# The bridge treats the waiter as gone when the heartbeat is older than this.
HEARTBEAT_STALE_SECONDS = 15.0


def default_task_db() -> Path:
    state_dir = Path(os.environ.get("A2A_STATE_DIR") or ROOT / "state")
    return state_dir.expanduser() / "tasks.sqlite3"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_heartbeat(state_dir: Path) -> dict[str, object] | None:
    """Return the waiter heartbeat, or None when no waiter wrote one."""

    try:
        payload = json.loads((state_dir / HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def waiter_is_listening(state_dir: Path, now: float | None = None) -> bool:
    payload = read_heartbeat(state_dir)
    if payload is None or payload.get("waiting") is not True:
        return False
    updated = payload.get("updatedAt")
    if not isinstance(updated, (int, float)):
        return False
    return (now if now is not None else time.time()) - updated <= HEARTBEAT_STALE_SECONDS


def write_heartbeat(state_dir: Path, waiting: bool) -> None:
    payload = {
        "pid": os.getpid(),
        "sessionId": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "waiting": waiting,
        "updatedAt": time.time(),
    }
    temporary = state_dir / (HEARTBEAT_FILE + ".tmp")
    try:
        temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        temporary.replace(state_dir / HEARTBEAT_FILE)
    except OSError as exc:
        print(f"Cannot write the heartbeat in {state_dir}: {exc}", file=sys.stderr)


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
        f"conversation ({current}). If this is the same conversation, resumed "
        "under a new session ID, run ./mount-worker.sh again and ask the user "
        "to restart the listener with the command it prints. Use --force to "
        "take its tasks anyway."
    )


def connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"Task database not found: {path}. Is the listener running?")
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


def claim(connection: sqlite3.Connection, task_id: str) -> None:
    """Record that the conversation received the task, so the bridge keeps it."""

    try:
        connection.execute(
            "UPDATE attached_requests SET claimed_at = COALESCE(claimed_at, ?) "
            "WHERE task_id = ? AND reply_state IS NULL",
            (now_iso(), task_id),
        )
    except sqlite3.OperationalError:
        pass  # A bridge from before claimed_at existed; nothing to record.


def wait(path: Path, timeout: float, poll: float) -> int:
    deadline = None if timeout <= 0 else time.monotonic() + timeout
    state_dir = path.parent
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        while True:
            write_heartbeat(state_dir, waiting=True)
            row = None
            # The waiter may start before the user runs the listener.
            if path.is_file():
                with connect(path) as connection:
                    row = pending(connection)
                    if row is not None:
                        claim(connection, row["task_id"])
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
                    f"\nDo the task, then reply with: python a2a_inbox.py reply "
                    f"{row['task_id']} --file ANSWER.md\n"
                    "Then start `python a2a_inbox.py wait` again.",
                    file=sys.stderr,
                )
                return 0
            if deadline is not None and time.monotonic() >= deadline:
                print("No task arrived before the timeout.", file=sys.stderr)
                return 2
            time.sleep(poll)
    finally:
        write_heartbeat(state_dir, waiting=False)


def reply(path: Path, task_id: str, text: str, failed: bool) -> int:
    if not text.strip():
        raise SystemExit("The reply is empty.")
    state = "TASK_STATE_FAILED" if failed else "TASK_STATE_COMPLETED"
    with connect(path) as connection:
        cursor = connection.execute(
            "UPDATE attached_requests SET reply_state = ?, reply_text = ?, replied_at = ? "
            "WHERE task_id = ? AND reply_state IS NULL",
            (state, text, now_iso(), task_id),
        )
    if cursor.rowcount != 1:
        raise SystemExit(
            f"Task {task_id} is not waiting for a reply (already answered, "
            "expired, or unknown). Run `python a2a_inbox.py status`."
        )
    print(f"Reply stored for {task_id} ({state}).")
    print("Start `python a2a_inbox.py wait` again to receive the next task.")
    return 0


def status(path: Path) -> int:
    """Print what the worker conversation needs to know at a glance."""

    state_dir = path.parent
    listening = waiter_is_listening(state_dir)
    heartbeat = read_heartbeat(state_dir)
    print(f"Task database: {path} ({'present' if path.is_file() else 'missing'})")
    if listening:
        waiter = f"listening (PID {heartbeat.get('pid') if heartbeat else '?'})"
    else:
        waiter = "NOT running: start `python a2a_inbox.py wait` as a background command"
    print(f"Waiter:        {waiter}")
    rows: list[sqlite3.Row] = []
    if path.is_file():
        with connect(path) as connection:
            try:
                rows = connection.execute(
                    "SELECT task_id, created_at, claimed_at, prompt FROM attached_requests "
                    "WHERE reply_state IS NULL ORDER BY created_at, rowid"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
    if not rows:
        print("Pending tasks: none")
        return 0
    print(f"Pending tasks: {len(rows)} (answer each with `reply`)")
    for row in rows:
        claimed = row["claimed_at"] or "not yet received by `wait`"
        prompt = " ".join(str(row["prompt"]).split())
        print(f"  {row['task_id']}  created {row['created_at']}  received {claimed}")
        print(f"    {prompt[:160]}")
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
    reply_parser.add_argument(
        "--failed", nargs="?", const="", default=None, metavar="REASON",
        help="Report the task as failed. The reason may follow the flag, "
        "or come from TEXT, --file, or stdin.",
    )

    commands.add_parser("status", help="Show the waiter heartbeat and pending tasks.")

    args = parser.parse_args()
    path = (args.task_db or default_task_db()).expanduser().resolve()
    if args.command == "wait":
        check_session(path, args.force)
        raise SystemExit(wait(path, args.timeout, args.poll))
    if args.command == "status":
        raise SystemExit(status(path))
    sources = [
        value for value in (args.failed or None, args.text, args.file) if value is not None
    ]
    if len(sources) > 1:
        raise SystemExit("Give the reply only once: as --failed REASON, TEXT, or --file.")
    if args.file:
        text = args.file.read_text(encoding="utf-8")
    elif args.failed:
        text = args.failed
    elif args.text is not None:
        text = args.text
    else:
        text = sys.stdin.read()
    raise SystemExit(reply(path, args.task_id, text, args.failed is not None))


if __name__ == "__main__":
    main()
