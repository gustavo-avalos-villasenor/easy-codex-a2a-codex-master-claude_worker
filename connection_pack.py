"""Build the copy-ready connection information for the remote Codex master."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
from typing import Any


def read_session_id(path: Path | None) -> str:
    if path is None:
        return "(created when the first message arrives)"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return "(created when the first message arrives)"
    value = payload.get("sessionId") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value else "(created when the first message arrives)"


def _configured(value: str | None, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def build_connection_pack(
    *,
    base_url: str,
    context_id: str,
    repository_url: str | None = None,
    listen_host: str | None = None,
    port: int | str | None = None,
    cwd: Path | str | None = None,
    state_dir: Path | str | None = None,
    session_file: Path | str | None = None,
    task_db: Path | str | None = None,
    pid_file: Path | str | None = None,
    log_file: Path | str | None = None,
    session_id: str | None = None,
    mode: str = "standby",
    claude_cli: str | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
    tools: str | None = None,
) -> dict[str, Any]:
    normalized_base = base_url.rstrip("/")
    resolved_session_file = Path(session_file).expanduser() if session_file else None
    resolved_repository = _configured(
        repository_url,
        "(not configured; set A2A_REPOSITORY_URL)",
    )
    resolved_state_dir = Path(state_dir).expanduser() if state_dir else None
    resolved_pid_file = Path(pid_file).expanduser() if pid_file else None
    stop_command = "./stop-claude-worker.sh"
    if resolved_state_dir and resolved_pid_file:
        default_pid = resolved_state_dir / "claude-worker.pid"
        if resolved_pid_file != default_pid:
            stop_command = (
                f"A2A_STATE_DIR={shlex.quote(str(resolved_state_dir))} "
                f"A2A_PID_FILE={shlex.quote(str(resolved_pid_file))} "
                "./stop-claude-worker.sh"
            )
    return {
        "protocolVersion": "1.0",
        "worker": "Claude Code",
        "role": "persistent Claude worker",
        "transport": "A2A 1.0 over HTTP",
        "repositoryUrl": resolved_repository,
        "baseUrl": normalized_base,
        "agentCardUrl": f"{normalized_base}/.well-known/agent-card.json",
        "jsonRpcUrl": f"{normalized_base}/",
        "contextId": context_id,
        "sessionId": session_id or read_session_id(resolved_session_file),
        "listenHost": listen_host or "(not specified)",
        "port": str(port) if port is not None else "(not specified)",
        "workingDirectory": str(Path(cwd).expanduser()) if cwd else "(not specified)",
        "stateDirectory": str(resolved_state_dir) if resolved_state_dir else "(not specified)",
        "sessionFile": str(resolved_session_file) if resolved_session_file else "(not specified)",
        "taskDatabase": str(Path(task_db).expanduser()) if task_db else "(not specified)",
        "pidFile": str(resolved_pid_file) if resolved_pid_file else "(not specified)",
        "logFile": str(Path(log_file).expanduser()) if log_file else "(not specified)",
        "stopCommand": stop_command,
        "mode": mode,
        "claudeCli": claude_cli or "claude (PATH/configured)",
        "model": model or "Claude defaults",
        "permissionMode": permission_mode or "acceptEdits",
        "tools": tools or "Claude defaults",
    }


def remote_agent_message(pack: dict[str, Any]) -> str:
    repository = pack["repositoryUrl"]
    repository_line = ""
    if not repository.startswith("("):
        repository_line = f"Worker repository: {repository}\n"
    base_url = pack["baseUrl"]
    context_id = pack["contextId"]
    return (
        "You are the Codex master and must communicate with a remote Claude "
        "Code worker through A2A 1.0 over HTTP.\n"
        f"{repository_line}"
        f"Read the Agent Card first: {pack['agentCardUrl']}\n"
        f"Use exactly this JSON-RPC endpoint: {pack['jsonRpcUrl']}\n"
        f"Use exactly this contextId: {context_id}\n\n"
        "The worker keeps one persistent Claude session and processes one "
        "request at a time. Do not assume that it is on localhost.\n\n"
        "For a short request, use an SSE connection until the response arrives:\n"
        f'python3 a2a_client.py "Your message for Claude" "{base_url}"\n\n'
        "For a long task, return control to the master and keep the task ID:\n"
        f'python3 a2a_client.py --submit "Your message for Claude" "{base_url}"\n'
        f'python3 a2a_client.py --wait "TASK-ID" "{base_url}"\n\n'
        "If this master is a Codex CLI conversation and you have its UUID, use "
        "--submit-and-resume. Do not run --wait or resend the same message "
        "after using that mode. If a connection drops, keep the task ID and "
        "reconnect with --wait; do not resubmit automatically."
    )


def format_connection_pack(pack: dict[str, Any]) -> str:
    lines = [
        "",
        "================ A2A connection instructions ================",
        f"Worker:          {pack['worker']} ({pack['role']})",
        f"Protocol:        {pack['transport']}",
        f"Repository:      {pack['repositoryUrl']}",
        f"Base URL:        {pack['baseUrl']}",
        f"Agent Card:      {pack['agentCardUrl']}",
        f"JSON-RPC URL:    {pack['jsonRpcUrl']}",
        f"A2A context ID:  {pack['contextId']}",
        f"Claude session:  {pack['sessionId']}",
        f"Listen host:     {pack['listenHost']}",
        f"TCP port:        {pack['port']}",
        f"Mode:            {pack['mode']}",
        f"Working dir:     {pack['workingDirectory']}",
        f"State dir:       {pack['stateDirectory']}",
        f"Session file:    {pack['sessionFile']}",
        f"Task database:   {pack['taskDatabase']}",
        f"PID file:        {pack['pidFile']}",
        f"Log file:        {pack['logFile']}",
        f"Claude CLI:      {pack['claudeCli']}",
        f"Claude model:    {pack['model']}",
        f"Permission mode: {pack['permissionMode']}",
        f"Claude tools:    {pack['tools']}",
        "",
        "Copy the following message to the remote Codex master:",
        remote_agent_message(pack),
        "",
        "To verify the connection from the master:",
        f'curl -fsS "{pack["agentCardUrl"]}"',
        "",
        "The worker is now in standby, waiting for requests.",
        "In standby mode, press Ctrl+C in this terminal to stop it.",
        f"In background mode, use {pack['stopCommand']} to stop it.",
        "",
        "==============================================================",
        "",
    ]
    return "\n".join(lines)


def _from_environment() -> dict[str, Any]:
    cwd = Path(os.environ.get("A2A_CLAUDE_CWD") or Path.cwd()).expanduser().resolve()
    state_dir = Path(os.environ.get("A2A_STATE_DIR") or (cwd / "state")).expanduser()
    port = int(os.environ.get("A2A_PORT", "8766"))
    host = os.environ.get("A2A_HOST", "127.0.0.1")
    base_url = os.environ.get("A2A_BASE_URL") or f"http://{host}:{port}"
    return build_connection_pack(
        base_url=base_url,
        context_id=os.environ.get("A2A_CONTEXT_ID", "claude-worker"),
        repository_url=os.environ.get("A2A_REPOSITORY_URL"),
        listen_host=host,
        port=port,
        cwd=cwd,
        state_dir=state_dir,
        session_file=state_dir / "claude-session.json",
        task_db=state_dir / "tasks.sqlite3",
        pid_file=Path(os.environ.get("A2A_PID_FILE") or state_dir / "claude-worker.pid"),
        log_file=Path(os.environ.get("A2A_LOG_FILE") or state_dir / "claude-worker.log"),
        mode=os.environ.get("A2A_WORKER_MODE", "standby"),
        claude_cli=os.environ.get("A2A_CLAUDE_CLI"),
        model=os.environ.get("A2A_CLAUDE_MODEL"),
        permission_mode=os.environ.get("A2A_CLAUDE_PERMISSION_MODE", "acceptEdits"),
        tools=os.environ.get("A2A_CLAUDE_TOOLS"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--context-id")
    parser.add_argument("--repository-url")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    pack = _from_environment()
    if args.base_url:
        pack["baseUrl"] = args.base_url.rstrip("/")
        pack["agentCardUrl"] = f"{pack['baseUrl']}/.well-known/agent-card.json"
        pack["jsonRpcUrl"] = f"{pack['baseUrl']}/"
    if args.context_id:
        pack["contextId"] = args.context_id
    if args.repository_url:
        pack["repositoryUrl"] = args.repository_url
    if args.as_json:
        print(json.dumps(pack, ensure_ascii=False, indent=2))
    else:
        print(format_connection_pack(pack), end="")


if __name__ == "__main__":
    main()
