# Handoff: Codex master and Claude worker

## Architecture

~~~text
Codex master
    |
    | A2A 1.0 over private HTTP (Tailscale/VPN/internal DNS)
    v
Python bridge in standby or background mode
    |
    | Claude Code CLI, one persistent session
    v
Claude worker on the worker host
~~~

The bridge publishes an Agent Card, accepts `SendMessage`,
`SendStreamingMessage`, `SubscribeToTask`, and `GetTask`, and stores tasks in
SQLite. Only one task runs Claude at a time. The Claude session is created by
the first message and its UUID is stored in `state/claude-session.json`; later
messages use `claude --resume` with that UUID.

With the `attached` executor the bridge does not start Claude. It stores
each task in the `attached_requests` table, and the Claude Code conversation
named by `--attached-session` answers it with `a2a_inbox.py wait` and
`a2a_inbox.py reply`. That conversation runs `mount-worker.sh`, which prints
the listener command for the user, as described in `CLAUDE.md`.
The session description above applies to the `headless` executor.

Communication is request and response based. Claude does not open new
conversations on its own or initiate messages to the master.

## Installation

From the repository root:

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
claude auth
~~~

Check the executables:

~~~bash
claude --version
codex --version
~~~

The worker does not need its own Codex conversation. Codex only needs access
to `a2a_client.py` to send requests.

## Run in standby

~~~bash
./start-claude-worker.sh
~~~

The command keeps the terminal occupied while it listens for requests. Stop
it with `Ctrl+C`, as with the original process. To return to the prompt and
detach it, use:

~~~bash
./start-claude-worker.sh --background
~~~

Default values for a same-machine test:

| Setting | Value |
| --- | --- |
| Host | 127.0.0.1 |
| Port | 8766 |
| contextId | claude-worker |
| State | state/ |
| Agent Card | http://127.0.0.1:8766/.well-known/agent-card.json |

Background mode stores the PID in `state/claude-worker.pid` and the log in
`state/claude-worker.log`. `--foreground` remains an explicit alias for
standby mode.

When Tailscale is installed, the launcher automatically uses its IPv4 address
unless `A2A_HOST` is set. For a local test, set `A2A_HOST=127.0.0.1`; to
disable detection, set `A2A_AUTO_TAILSCALE=0`.

Before entering standby, the launcher prints a complete pack with the
repository, Agent Card, JSON-RPC endpoint, context ID, Claude session, state
paths, and the copy-ready message for the remote Codex master. To print it
again after the first message, when the session UUID exists:

~~~bash
.venv/bin/python connection_pack.py
~~~

Stop the process with:

~~~bash
./stop-claude-worker.sh
~~~

The PID is validated against `a2a_bridge.py` before the signal is sent.

## Messages from Codex

Synchronous request:

~~~bash
python3 a2a_client.py \
  "Inspect the repository state and reply with a summary." \
  "WORKER_BASE_URL"
~~~

Durable request:

~~~bash
python3 a2a_client.py --submit \
  "Complete a long task and return the result." \
  "WORKER_BASE_URL"

python3 a2a_client.py --wait "TASK-ID" "WORKER_BASE_URL"
~~~

If the master is Codex CLI, `--submit-and-resume` can resume the master
conversation after Claude finishes:

~~~bash
python3 a2a_client.py --submit-and-resume \
  "Complete the task and report the changes." \
  "WORKER_BASE_URL" \
  "CODEX-CONVERSATION-UUID"
~~~

After using that mode, do not run `--wait` or submit the request again.

## Configuration

Available variables:

- `A2A_CLAUDE_CLI`: alternate Claude executable path or name.
- `A2A_CLAUDE_MODEL`: Claude model or alias.
- `A2A_CLAUDE_EFFORT`: `low`, `medium`, `high`, `xhigh`, or `max`.
- `A2A_CLAUDE_PERMISSION_MODE`: defaults to `acceptEdits`.
- `A2A_CLAUDE_TOOLS`: allowed tools, such as `Read,Edit,Bash`.
- `A2A_CLAUDE_SYSTEM_PROMPT`: additional system prompt.
- `A2A_CLAUDE_TIMEOUT`: timeout in seconds; `0` means no limit.
- `A2A_HOST`, `A2A_PORT`, `A2A_BASE_URL`, and `A2A_CONTEXT_ID`: A2A transport.
- `A2A_AUTO_TAILSCALE`: automatic Tailscale IPv4 detection; defaults to `1`.
- `A2A_STATE_DIR`: state, PID, log, and SQLite directory.
- `A2A_REPOSITORY_URL`: URL printed and handed to the remote master.
- `A2A_EXECUTOR`: `auto` (default), `attached`, or `headless`.

Example for a machine reachable from another private network:

~~~bash
A2A_HOST=100.64.10.20 \
A2A_BASE_URL=http://100.64.10.20:8766 \
A2A_REPOSITORY_URL=https://github.com/OWNER/REPO \
A2A_CLAUDE_PERMISSION_MODE=acceptEdits \
./start-claude-worker.sh --background
~~~

The `bypassPermissions` mode must be enabled explicitly and allows Claude to
skip permission controls. Keep the bridge inside a controlled private
network; the bridge does not provide application authentication.

## Recovery

If the bridge restarts, unfinished tasks are marked as failed. They are not
resumed automatically because a task may have edited files before the
interruption. Review the repository and send a corrected instruction only
after confirming its state.

If a client connection is lost, keep the task ID and use `--wait`. The client
does not resubmit the request automatically.

## Tests

~~~bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
~~~

The Claude tests use a fake executable to verify session creation and resume.
The real integration check starts the worker and sends messages with
`a2a_client.py`.

