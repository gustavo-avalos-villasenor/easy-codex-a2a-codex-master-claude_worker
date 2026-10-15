# Codex master + Claude worker

> **Claude Code agent asked to mount this worker:** follow
> [CLAUDE.md](CLAUDE.md). In short: clone into a permanent directory, run
> `./mount-worker.sh`, give the user the command it prints, and start
> `.venv/bin/python a2a_inbox.py wait` as a background command. The user runs
> the listener; it prints complete instructions for the Codex master and saves
> them to `state/connection-pack.txt`. Tasks from the master arrive in your
> conversation and you answer them there. Your waiter dies whenever the
> conversation restarts: start it again every time (CLAUDE.md, "Recover").
>
> **Codex master:** use the message the listener prints (or
> `state/connection-pack.txt`); [REMOTE_AGENT_PROMPT.md](REMOTE_AGENT_PROMPT.md)
> explains every task state.

This repository exposes a persistent Claude Code session as an A2A 1.0
worker. Codex acts as the master: it sends messages to the bridge over HTTP
and receives the worker's responses. The master may run on another machine;
the remote master must use the URL advertised by `A2A_BASE_URL`. The local
value `127.0.0.1` is only the default for a same-machine test.

Two executors answer the tasks:

- `attached`: the bridge queues each task and a Claude Code conversation
  answers it with `a2a_inbox.py wait` / `a2a_inbox.py reply`. Selected by
  `--attached-session UUID` (the command `mount-worker.sh` prints) or when the
  launcher runs inside Claude Code. See [CLAUDE.md](CLAUDE.md).
  The conversation must stay open with its waiter running. The Agent Card
  reports `metadata.attachedWorker` (`listening`, `busy`, or `not listening`),
  and a task that no waiter receives within `A2A_ATTACHED_CLAIM_TIMEOUT`
  seconds (default 120) fails with a clear reason instead of staying in
  `TASK_STATE_WORKING` forever.
- `headless` (default from a plain terminal, or with `--headless`): the bridge
  runs a separate persistent Claude Code session with `claude --print`.

The bridge keeps its state in `state/`:

- `state/tasks.sqlite3`: A2A tasks and their states.
- `state/claude-session.json`: the persistent Claude Code session UUID
  (headless executor only).
- `state/connection-pack.txt`: the instructions for the master, written each
  time the listener starts.
- `state/attached-session.json` and `state/inbox-heartbeat.json`: the
  conversation the worker is mounted on and its waiter's heartbeat (attached
  executor only).

Run the listener and the worker conversation as an OS user that can write
`state/` (normally the owner of the repository directory).

Each message is processed sequentially within the same Claude session. After
the first message, later messages use Claude's `--resume` option, so the
worker preserves the conversation context.

## Requirements

- Python 3.10 or newer.
- Claude Code installed and authenticated (`claude auth`).
- The dependencies listed in `requirements.txt`.
- Codex CLI if the master will use `--submit-and-resume`.
- A private network reachable by the master and worker when they run on
  separate machines, such as Tailscale, a VPN, or a private network protected
  by a firewall.

Install the project:

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
~~~

## Start the worker in standby

~~~bash
./start-claude-worker.sh
~~~

The launcher stays attached to the terminal, like the original process. The
bridge remains in standby until the Codex master sends a message; press
`Ctrl+C` to stop it. To detach it and return to the prompt:

~~~bash
./start-claude-worker.sh --background
~~~

Background mode prints the PID, the Agent Card, and the log path at
`state/claude-worker.log`.

Before entering standby, the launcher prints a complete connection pack with
the repository, base URL, Agent Card, JSON-RPC endpoint, context ID, Claude
session, state paths, configuration, and copy-ready instructions for the
remote master. It also saves the pack to `state/connection-pack.txt`, so you
never need to scroll back. To print the saved pack again:

~~~bash
.venv/bin/python connection_pack.py
~~~

Stop the worker with:

~~~bash
./stop-claude-worker.sh
~~~

The port and context ID are also accepted as positional arguments:

~~~bash
./start-claude-worker.sh 8766 claude-worker
~~~

For a remote worker, explicitly configure the listening interface and the URL
that the master will use. The URL may be a Tailscale IP address or a private
DNS name; do not use `127.0.0.1` for a remote master:

~~~bash
./start-claude-worker.sh \
  --host 100.64.10.20 \
  --base-url http://100.64.10.20:8766 \
  --context-id claude-worker-prod \
  --repository-url https://github.com/OWNER/REPO
~~~

You can also set `A2A_HOST`, `A2A_BASE_URL`, `A2A_CONTEXT_ID`, and
`A2A_REPOSITORY_URL` in the environment. The worker host firewall and the
private network ACLs must allow the configured bind address and port.

When Tailscale is installed, the launcher automatically uses its IPv4 address
when `A2A_HOST` is not set. Force localhost with `A2A_HOST=127.0.0.1`, or
disable automatic detection with `A2A_AUTO_TAILSCALE=0`.

## Send messages from the Codex master

Read the Agent Card:

~~~bash
curl http://WORKER_HOST:8766/.well-known/agent-card.json
~~~

The master must copy the endpoint and `contextId` from the card. Do not invent
a different context ID.

For a short request, keep the connection open until Claude finishes:

~~~bash
python3 a2a_client.py \
  "Read README.md and reply with one sentence confirming that you are the worker." \
  "http://WORKER_HOST:8766"
~~~

For a long task, submit it and let the master continue without waiting in the
same turn:

~~~bash
python3 a2a_client.py --submit \
  "Inspect the repository and propose the next change." \
  "http://WORKER_HOST:8766"

python3 a2a_client.py --wait TASK-ID-RETURNED "http://WORKER_HOST:8766"
~~~

If the master is a Codex CLI conversation, it can use `--submit-and-resume`
with that conversation's UUID. The local watcher resumes the master once after
Claude finishes:

~~~bash
python3 a2a_client.py --submit-and-resume \
  "Complete the requested task and return a report." \
  "http://WORKER_HOST:8766" \
  "CODEX-CONVERSATION-UUID"
~~~

If the connection drops, keep the task ID and use `--wait`; do not submit the
same request again.

## Worker configuration

The most useful variables are:

- `A2A_CLAUDE_MODEL`: Claude model or alias.
- `A2A_CLAUDE_EFFORT`: `low`, `medium`, `high`, `xhigh`, or `max`.
- `A2A_CLAUDE_PERMISSION_MODE`: defaults to `acceptEdits`.
- `A2A_CLAUDE_TOOLS`: tool list, such as `Read,Edit,Bash`.
- `A2A_CLAUDE_TIMEOUT`: seconds; `0` means no limit.
- `A2A_STATE_DIR`: directory for SQLite, session, PID, and logs.
- `A2A_REPOSITORY_URL`: repository URL included in the connection pack.
- `A2A_WORKER_MODE`: mode label shown in the connection pack.
- `A2A_EXECUTOR`: `auto` (default), `attached`, or `headless`.
- `A2A_ATTACHED_SESSION`: Claude Code session UUID for the attached executor.
- `A2A_ATTACHED_CLAIM_TIMEOUT`: seconds a task may wait for the attached
  conversation to receive it before it fails (default `120`; `0` disables).
- `A2A_REPOSITORY_URL` defaults to the `origin` remote, so the master's
  instructions include where to clone the client.

Example:

~~~bash
A2A_CLAUDE_MODEL=sonnet \
A2A_CLAUDE_PERMISSION_MODE=acceptEdits \
./start-claude-worker.sh
~~~

The `bypassPermissions` mode must be configured explicitly. Use it only when
the worker host and master requests are controlled.

The bridge does not provide application authentication. For remote use, keep
the endpoint inside Tailscale or a VPN, or protect it with a firewall and ACL;
do not expose it directly to the public Internet.

## Main files

- `a2a_bridge_claude.py`: A2A bridge and persistent Claude worker.
- `a2a_client.py`: standard client for the Codex master.
- `mount-worker.sh`: run by the worker conversation; prints the listener
  command bound to that conversation.
- `a2a_inbox.py`: wait for (`wait`), answer (`reply`), and inspect
  (`status`) tasks in attached mode.
- `connection_pack.py`: prints the complete handoff for the remote master
  (the copy the running listener saved, or `--rebuild` from the environment).
- `start-claude-worker.sh`: Linux/macOS launcher for standby or background
  mode.
- `stop-claude-worker.sh`: safe shutdown through the registered PID.
- `a2a_bridge.py`: main entry point.
- `test_claude_worker.py`: Claude session lifecycle tests.
- `test_async_resume.py`: Codex client callback watcher tests.
- `test_attached_worker.py`: attached executor and inbox tests.

## Troubleshooting the attached worker

| Symptom | Cause | Fix |
| --- | --- | --- |
| The master's task stays in `TASK_STATE_WORKING` with "Queued", then fails with "is not listening" | The worker conversation's waiter is not running (the conversation was closed, restarted, or resumed) | In the worker conversation: `a2a_inbox.py status`, answer any pending task, start `a2a_inbox.py wait` again. Then resend from the master. |
| The Agent Card says `"attachedWorker": "not listening"` | Same as above | Same as above |
| `a2a_inbox.py wait` says the worker "is mounted on Claude Code session X, not on this conversation" | The conversation was resumed under a new session ID | Run `./mount-worker.sh` again and restart the listener with the new command it prints |
| `a2a_inbox.py status` says the task database is missing | The listener is not running | Start it again with the command from `mount-worker.sh` |
| The listener's output scrolled away | Long output in a terminal without scrollback | Open `state/connection-pack.txt`, or run `.venv/bin/python connection_pack.py` |
| The listener cannot write `state/` | It runs as a different OS user than the owner of the repository | Run it as the owner, or `chown -R` the repository to that user |
