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
    executor: str = "headless",
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
        "executor": executor,
        "claudeCli": claude_cli or "claude (PATH/configured)",
        "model": model or "Claude defaults",
        "permissionMode": permission_mode or "acceptEdits",
        "tools": tools or "Claude defaults",
    }


def remote_agent_message(pack: dict[str, Any]) -> str:
    """Self-contained instructions: the master needs nothing else."""

    repository = pack["repositoryUrl"]
    base_url = pack["baseUrl"]
    context_id = pack["contextId"]
    if repository.startswith("("):
        get_client = (
            "1. The worker did not publish its repository URL, so you cannot "
            "fetch a2a_client.py. Use the raw JSON-RPC calls in step 4.\n"
        )
    else:
        folder = repository.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "a2a-worker"
        get_client = (
            "1. Get the client. It needs Python 3.10+ and only the standard "
            "library (no pip install):\n"
            f"   git clone {repository} {folder} || git -C {folder} pull\n"
            f"   cd {folder}\n"
        )
    attached = pack.get("executor") == "attached"
    who = (
        "a live Claude Code conversation that a person is watching"
        if attached
        else "a persistent headless Claude Code session"
    )
    send_message = (
        '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":'
        f'{{"role":"ROLE_USER","contextId":"{context_id}","parts":[{{"text":"YOUR MESSAGE"}}]}},'
        '"configuration":{"returnImmediately":true}}}'
    )
    get_task = (
        '{"jsonrpc":"2.0","id":2,"method":"GetTask","params":{"id":"TASK-ID"}}'
    )
    return (
        "You are the Codex master. A Claude Code worker is waiting for your "
        "requests over A2A 1.0 (JSON-RPC over HTTP). Everything you need is "
        "below; do not ask for further setup.\n\n"
        f"Agent Card:        {pack['agentCardUrl']}\n"
        f"JSON-RPC endpoint: {pack['jsonRpcUrl']}\n"
        f"contextId:         {context_id} (fixed; do not invent another one)\n\n"
        f"{get_client}"
        "2. Check that the worker is reachable:\n"
        f'   curl -fsS "{pack["agentCardUrl"]}"\n'
        "3. Send requests with a2a_client.py from that folder. It reads the "
        "endpoint and contextId from the Agent Card; do not build JSON-RPC by "
        "hand while the client is available.\n"
        "   Short request (waits for the answer):\n"
        f'     python3 a2a_client.py "Your message for Claude" "{base_url}"\n'
        "   Long task (returns a taskId at once; collect the answer later):\n"
        f'     python3 a2a_client.py --submit "Your message for Claude" "{base_url}"\n'
        f'     python3 a2a_client.py --wait "TASK-ID" "{base_url}"\n'
        "   If you are a Codex CLI conversation and know your own conversation "
        "UUID, --submit-and-resume submits the task and resumes your "
        "conversation once with the answer:\n"
        f'     python3 a2a_client.py --submit-and-resume "Your message" "{base_url}" "YOUR-CODEX-CONVERSATION-UUID"\n'
        "     After --submit-and-resume, do not run --wait or resubmit.\n"
        "4. Only without the client, use raw JSON-RPC:\n"
        f"   curl -fsS -H 'Content-Type: application/json' -d '{send_message}' {pack['jsonRpcUrl']}\n"
        "   Take result.task.id from the reply and repeat until "
        "result.task.status.state is TASK_STATE_COMPLETED or TASK_STATE_FAILED; "
        "the answer is in result.task.status.message.parts[0].text:\n"
        f"   curl -fsS -H 'Content-Type: application/json' -d '{get_task}' {pack['jsonRpcUrl']}\n\n"
        "Rules:\n"
        f"- The worker is {who}. It keeps context between your messages, "
        "handles one task at a time, and may take minutes to answer.\n"
        "- Use the URLs above exactly. Do not assume that it is on localhost.\n"
        "- If a connection drops, keep the taskId and reconnect with --wait "
        "(or GetTask). Never resend the same request: the worker may already "
        "be doing it.\n"
        "- A task can end in TASK_STATE_FAILED with a reason, for example when "
        "the worker declines it. Read the reason before sending a new request."
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
        f"Executor:        {pack['executor']}",
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
        *(
            [
                f"Attached executor: Claude Code session {pack['sessionId']} answers",
                "each task. That conversation must keep `a2a_inbox.py wait` running.",
            ]
            if pack.get("executor") == "attached"
            else []
        ),
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
        executor=os.environ.get("A2A_EXECUTOR", "headless"),
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
