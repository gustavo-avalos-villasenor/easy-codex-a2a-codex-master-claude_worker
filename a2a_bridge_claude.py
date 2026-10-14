"""Small A2A 1.0 bridge for a persistent Claude Code worker.

The transport uses only Python's standard library so the worker can be started
without downloading a web framework. The client-facing protocol is
JSON-RPC plus Server-Sent Events.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
from uuid import UUID, uuid4

from connection_pack import build_connection_pack, format_connection_pack


TERMINAL_STATES = frozenset(
    {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
    }
)


def claude_command() -> list[str]:
    """Find the Claude Code executable for the worker process."""

    configured = os.environ.get("A2A_CLAUDE_CLI", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return [str(path)]
        resolved = shutil.which(configured)
        if resolved:
            return [resolved]
        raise RuntimeError(
            f"Configured Claude executable not found in A2A_CLAUDE_CLI: {configured}"
        )

    executable = shutil.which("claude")
    if executable:
        return [executable]
    raise RuntimeError(
        "Claude Code CLI not found. Install it or set A2A_CLAUDE_CLI."
    )


def normalized_session_id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(UUID(value.strip()))
    except ValueError:
        return None


def text_from_value(value: object) -> str:
    """Extract readable text from Claude's JSON result shapes."""

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            piece = text_from_value(item)
            if piece:
                pieces.append(piece)
        return "\n".join(pieces)
    if isinstance(value, dict):
        for key in ("text", "result", "message", "content"):
            if key in value:
                piece = text_from_value(value[key])
                if piece:
                    return piece
    return ""


class ClaudeWorker:
    """Run sequential prompts against one persisted Claude Code session."""

    def __init__(
        self,
        cwd: Path,
        session_file: Path,
        timeout: int | None,
        model: str | None,
        effort: str | None,
        permission_mode: str,
        tools: str | None,
        system_prompt: str | None,
    ) -> None:
        self.cwd = cwd
        self.session_file = session_file
        self.timeout = timeout
        self.model = model
        self.effort = effort
        self.permission_mode = permission_mode
        self.tools = tools
        self.system_prompt = system_prompt
        self.session_id = self._read_session_id()

    def _read_session_id(self) -> str | None:
        try:
            payload = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return None
        if isinstance(payload, dict):
            return normalized_session_id(payload.get("sessionId"))
        return None

    def _write_session_id(self, session_id: str) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessionId": session_id,
            "cwd": str(self.cwd),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        temporary = self.session_file.with_name(
            f".{self.session_file.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.session_file)
        try:
            self.session_file.chmod(0o600)
        except OSError:
            pass

    def _command(self, session_id: str, prompt: str) -> list[str]:
        command = claude_command() + [
            "--print",
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
            "--permission-prompts",
            "none",
        ]
        if self.permission_mode == "bypassPermissions":
            command.append("--dangerously-skip-permissions")
        if self.model:
            command.extend(["--model", self.model])
        if self.effort:
            command.extend(["--effort", self.effort])
        if self.tools is not None:
            # Claude declares --tools as a variadic option. The equals form
            # keeps the positional prompt from being consumed as another tool.
            command.append(f"--tools={self.tools}")
        if self.system_prompt:
            command.extend(["--append-system-prompt", self.system_prompt])
        if self.session_id:
            command.extend(["--resume", session_id])
        else:
            command.extend(["--session-id", session_id])
        command.append(prompt)
        return command

    @staticmethod
    def _parse_output(stdout: str) -> dict[str, object] | None:
        candidate = stdout.strip()
        if not candidate:
            return None
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return payload
        for line in reversed(candidate.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def ask(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("Send a non-empty text message.")

        session_id = self.session_id or str(uuid4())
        command = self._command(session_id, prompt)
        process_timeout = (
            None if self.timeout is None or self.timeout <= 0 else self.timeout
        )
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=process_timeout,
                cwd=self.cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Claude exceeded the {process_timeout} second timeout."
            ) from exc

        payload = self._parse_output(result.stdout)
        if result.returncode:
            detail = "\n".join(
                filter(
                    None,
                    [
                        result.stderr.strip(),
                        text_from_value(payload.get("errors") if payload else None),
                        text_from_value(payload.get("result") if payload else None),
                    ],
                )
            )
            raise RuntimeError(
                f"Claude exited with code {result.returncode}: "
                f"{detail[:2000] or 'no diagnostic information'}"
            )

        if payload and payload.get("is_error"):
            detail = text_from_value(payload.get("result") or payload.get("errors"))
            raise RuntimeError(f"Claude returned an error: {detail or 'no diagnostic information'}")

        returned_session_id = normalized_session_id(
            payload.get("session_id") if payload else None
        )
        active_session_id = returned_session_id or session_id
        answer = text_from_value(payload.get("result") if payload else None)
        if not answer:
            answer = text_from_value(payload.get("message") if payload else None)
        if not answer:
            answer = result.stdout.strip()
        if not answer:
            raise RuntimeError("Claude finished without returning a response.")

        self.session_id = active_session_id
        self._write_session_id(active_session_id)
        return answer


@dataclass
class TaskRecord:
    task_id: str
    context_id: str
    prompt: str
    state: str = "TASK_STATE_SUBMITTED"
    text: str = ""
    created_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    updated_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    working: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)

    def as_task(self) -> dict[str, object]:
        status: dict[str, object] = {"state": self.state}
        if self.text:
            status["message"] = {
                "role": "ROLE_AGENT",
                "parts": [{"text": self.text}],
            }
        return {
            "id": self.task_id,
            "contextId": self.context_id,
            "status": status,
        }

    def as_status_update(self) -> dict[str, object]:
        status: dict[str, object] = {"state": self.state}
        if self.text:
            status["message"] = {
                "role": "ROLE_AGENT",
                "parts": [{"text": self.text}],
            }
        return {
            "taskId": self.task_id,
            "contextId": self.context_id,
            "status": status,
        }


class TaskStore:
    """Durable task metadata with one serialized Claude worker queue."""

    def __init__(
        self,
        path: Path,
        context_id: str,
        worker: ClaudeWorker | None,
        demo: bool,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.context_id = context_id
        self.worker = worker
        self.demo = demo
        self.lock = threading.RLock()
        self.tasks: dict[str, TaskRecord] = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="claude-worker")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self._load_tasks()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    context_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    state TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _load_tasks(self) -> None:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, context_id, prompt, state, text, created_at, updated_at "
                "FROM tasks ORDER BY created_at"
            ).fetchall()
        with self.lock:
            for row in rows:
                record = TaskRecord(
                    task_id=row["id"],
                    context_id=row["context_id"],
                    prompt=row["prompt"],
                    state=row["state"],
                    text=row["text"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                if record.state in TERMINAL_STATES:
                    record.done.set()
                elif record.state == "TASK_STATE_WORKING":
                    record.working.set()
                self.tasks[record.task_id] = record

        interrupted = [
            record
            for record in self.tasks.values()
            if record.state not in TERMINAL_STATES
        ]
        for record in interrupted:
            self.update(
                record,
                "TASK_STATE_FAILED",
                "The bridge restarted while Claude was working. "
                "The task was not resumed to avoid duplicating changes.",
            )

    def _save(self, record: TaskRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks
                    (id, context_id, prompt, state, text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state=excluded.state,
                    text=excluded.text,
                    updated_at=excluded.updated_at
                """,
                (
                    record.task_id,
                    record.context_id,
                    record.prompt,
                    record.state,
                    record.text,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def create(self, prompt: str) -> TaskRecord:
        record = TaskRecord(str(uuid4()), self.context_id, prompt)
        with self.lock:
            self.tasks[record.task_id] = record
            self._save(record)
            self.executor.submit(self._run, record)
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        with self.lock:
            return self.tasks.get(task_id)

    def update(self, record: TaskRecord, state: str, text: str = "") -> None:
        with self.lock:
            record.state = state
            record.text = text
            record.updated_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            self._save(record)
            if state == "TASK_STATE_WORKING":
                record.working.set()
            if state in TERMINAL_STATES:
                record.done.set()

    def _run(self, record: TaskRecord) -> None:
        self.update(
            record,
            "TASK_STATE_WORKING",
            "Claude is processing this request.",
        )
        try:
            if self.demo:
                answer = "Demo Claude A2A: " + record.prompt
            else:
                if self.worker is None:
                    raise RuntimeError("The Claude worker is not configured.")
                answer = self.worker.ask(record.prompt)
        except Exception as exc:  # noqa: BLE001
            logging.exception("A2A task %s failed", record.task_id)
            self.update(record, "TASK_STATE_FAILED", f"Claude request failed: {exc}")
        else:
            self.update(record, "TASK_STATE_COMPLETED", answer)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


def agent_card(
    base_url: str,
    context_id: str,
    demo: bool,
    *,
    repository_url: str | None = None,
) -> dict[str, object]:
    name = "Claude demo worker" if demo else "Claude Code worker"
    card: dict[str, object] = {
        "protocolVersion": "1.0",
        "name": name,
        "description": (
            f"Persistent Claude Code worker; contextId: {context_id}; "
            "transport: A2A 1.0 over HTTP"
        ),
        "version": "1.0.0",
        "supportedInterfaces": [
            {
                "url": base_url.rstrip("/") + "/",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "claude-worker",
                "name": "Claude worker",
                "description": "Processes messages in one persistent Claude Code session.",
                "tags": ["claude", "worker", "a2a"],
            }
        ],
    }
    metadata: dict[str, object] = {
        "contextId": context_id,
        "worker": "Claude Code",
        "transport": "A2A 1.0 over HTTP",
    }
    if repository_url:
        card["documentationUrl"] = repository_url
        metadata["repositoryUrl"] = repository_url
    card["metadata"] = metadata
    return card


def message_prompt(message: dict[str, object]) -> str:
    parts = message.get("parts", message.get("content", []))
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        str(part["text"])
        for part in parts
        if isinstance(part, dict) and "text" in part
    )


class BridgeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        state: TaskStore,
        card: dict[str, object],
    ) -> None:
        super().__init__(address, A2ARequestHandler)
        self.state = state
        self.card = card


class A2ARequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: object) -> None:
        logging.info("%s - %s", self.address_string(), format_string % args)

    @property
    def bridge(self) -> BridgeHTTPServer:
        return self.server  # type: ignore[return-value]

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()

    def _send_error(self, request_id: object, message: str, code: int = -32600) -> None:
        self._send_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            },
            status=400,
        )

    def _send_sse_header(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send_sse(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(
            {"jsonrpc": "2.0", **payload},
            ensure_ascii=False,
        ).encode("utf-8")
        self.wfile.write(b"data: " + encoded + b"\n\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/.well-known/agent-card.json":
            self._send_json(
                {"error": "not found"},
                status=404,
            )
            return
        self._send_json(self.bridge.card)

    def _read_request(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _validated_message(
        self,
        params: dict[str, object],
    ) -> tuple[str, str] | tuple[None, str]:
        message = params.get("message")
        if not isinstance(message, dict):
            return None, "params.message is required."
        context_id = message.get("contextId", message.get("context_id"))
        if context_id != self.bridge.state.context_id:
            return None, (
                f"This bridge is dedicated to contextId "
                f"{self.bridge.state.context_id}."
            )
        prompt = message_prompt(message)
        if not prompt.strip():
            return None, "Send a non-empty text message."
        return prompt, ""

    def _create_task(
        self,
        request_id: object,
        params: dict[str, object],
    ) -> TaskRecord | None:
        prompt, error = self._validated_message(params)
        if prompt is None:
            self._send_error(request_id, error)
            return None
        return self.bridge.state.create(prompt)

    def do_POST(self) -> None:  # noqa: N802
        request = self._read_request()
        if request is None:
            self._send_error(None, "Invalid JSON-RPC request.")
            return

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            self._send_error(request_id, "method and params are required.")
            return

        if method == "SendMessage":
            record = self._create_task(request_id, params)
            if record is None:
                return
            configuration = params.get("configuration")
            return_immediately = (
                isinstance(configuration, dict)
                and configuration.get("returnImmediately") is True
            )
            if not return_immediately:
                record.done.wait()
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"task": record.as_task()},
                }
            )
            return

        if method == "SendStreamingMessage":
            record = self._create_task(request_id, params)
            if record is None:
                return
            self._send_sse_header()
            try:
                self._send_sse({"id": request_id, "result": {"task": record.as_task()}})
                record.working.wait()
                self._send_sse(
                    {
                        "id": request_id,
                        "result": {"statusUpdate": record.as_status_update()},
                    }
                )
                record.done.wait()
                self._send_sse(
                    {
                        "id": request_id,
                        "result": {"statusUpdate": record.as_status_update()},
                    }
                )
            except (BrokenPipeError, ConnectionResetError):
                logging.info("Client closed the stream for %s.", record.task_id)
            return

        if method == "SubscribeToTask":
            task_id = params.get("id")
            if not isinstance(task_id, str):
                self._send_error(request_id, "params.id is required.")
                return
            record = self.bridge.state.get(task_id)
            if record is None:
                self._send_error(request_id, f"Task {task_id} does not exist.", -32004)
                return
            self._send_sse_header()
            try:
                self._send_sse({"id": request_id, "result": {"task": record.as_task()}})
                if record.state not in TERMINAL_STATES:
                    record.done.wait()
                    self._send_sse(
                        {
                            "id": request_id,
                            "result": {"statusUpdate": record.as_status_update()},
                        }
                    )
            except (BrokenPipeError, ConnectionResetError):
                logging.info("Client closed the stream for %s.", record.task_id)
            return

        if method == "GetTask":
            task_id = params.get("id")
            record = self.bridge.state.get(task_id) if isinstance(task_id, str) else None
            if record is None:
                self._send_error(request_id, "Requested task does not exist.", -32004)
                return
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"task": record.as_task()},
                }
            )
            return

        self._send_error(request_id, f"Unsupported A2A method: {method}", -32601)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("A2A_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("A2A_PORT", "8766")))
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--context-id",
        default=os.environ.get("A2A_CONTEXT_ID", "claude-worker"),
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path(os.environ.get("A2A_CLAUDE_CWD") or Path.cwd()),
    )
    parser.add_argument("--session-file", type=Path, default=None)
    parser.add_argument("--task-db", type=Path, default=None)
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("A2A_CLAUDE_TIMEOUT", "0")),
    )
    parser.add_argument("--model", default=os.environ.get("A2A_CLAUDE_MODEL") or None)
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=os.environ.get("A2A_CLAUDE_EFFORT") or None,
    )
    parser.add_argument(
        "--permission-mode",
        choices=(
            "acceptEdits",
            "auto",
            "bypassPermissions",
            "dontAsk",
            "manual",
            "plan",
        ),
        default=os.environ.get("A2A_CLAUDE_PERMISSION_MODE", "acceptEdits"),
    )
    parser.add_argument("--tools", default=os.environ.get("A2A_CLAUDE_TOOLS"))
    parser.add_argument(
        "--system-prompt",
        default=os.environ.get("A2A_CLAUDE_SYSTEM_PROMPT") or None,
    )
    parser.add_argument(
        "--repository-url",
        default=os.environ.get("A2A_REPOSITORY_URL") or None,
    )
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        raise NotADirectoryError(f"Working directory does not exist: {cwd}")

    state_dir = Path(os.environ.get("A2A_STATE_DIR") or (cwd / "state"))
    session_file = (
        args.session_file.expanduser()
        if args.session_file
        else state_dir / "claude-session.json"
    )
    task_db = (
        args.task_db.expanduser()
        if args.task_db
        else state_dir / "tasks.sqlite3"
    )
    base_url = (
        args.base_url
        or os.environ.get("A2A_BASE_URL")
        or f"http://{args.host}:{args.port}"
    ).rstrip("/")

    worker = None
    if not args.demo:
        cli = claude_command()
        worker = ClaudeWorker(
            cwd=cwd,
            session_file=session_file,
            timeout=args.timeout,
            model=args.model,
            effort=args.effort,
            permission_mode=args.permission_mode,
            tools=args.tools,
            system_prompt=args.system_prompt,
        )
        print(f"Claude CLI:          {' '.join(cli)}", flush=True)
        print(
            f"Claude session:      {worker.session_id or '(created on first message)'}",
            flush=True,
        )

    resolved_task_db = task_db.expanduser().resolve()
    state = TaskStore(resolved_task_db, args.context_id, worker, args.demo)
    card = agent_card(
        base_url,
        args.context_id,
        args.demo,
        repository_url=args.repository_url,
    )
    server = BridgeHTTPServer((args.host, args.port), state, card)

    connection = build_connection_pack(
        base_url=base_url,
        context_id=args.context_id,
        repository_url=args.repository_url,
        listen_host=args.host,
        port=args.port,
        cwd=cwd,
        state_dir=state_dir,
        session_file=session_file,
        task_db=resolved_task_db,
        pid_file=Path(os.environ.get("A2A_PID_FILE") or state_dir / "claude-worker.pid"),
        log_file=Path(os.environ.get("A2A_LOG_FILE") or state_dir / "claude-worker.log"),
        session_id=worker.session_id if worker else None,
        mode=os.environ.get("A2A_WORKER_MODE", "standby"),
        claude_cli=" ".join(cli) if not args.demo else "demo",
        model=args.model,
        permission_mode=args.permission_mode,
        tools=args.tools,
    )
    print(format_connection_pack(connection), end="", flush=True)

    logging.basicConfig(level=logging.INFO)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Stopping Claude worker...", flush=True)
    finally:
        server.server_close()
        state.close()


if __name__ == "__main__":
    main()
