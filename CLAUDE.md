# Instructions for the Claude Code agent that mounts this worker

If a user asks you to set yourself up as the worker ("móntate como worker",
"run as the worker"), the worker is **this conversation**. Do not hand the
tasks to a separate Claude session: the user expects to see each request from
the Codex master and your answer here, in the conversation that started the
bridge.

## Start

1. `python3 -m venv .venv` if `.venv/` does not exist.
2. `./start-claude-worker.sh --background`

   Launched from Claude Code, the bridge selects the `attached` executor
   automatically (it reads `CLAUDE_CODE_SESSION_ID`). Pass `--attached` to make
   it explicit. Use `--background`: a foreground launcher would block your
   tool call. If Tailscale is installed, the bridge listens on its IPv4
   address; otherwise pass `--host` and `--base-url`.
3. Show the user the connection pack (Agent Card URL, JSON-RPC URL,
   `contextId`) so they can give it to the Codex master.

## Serve tasks

Keep exactly one inbox waiter running while the worker is active:

~~~bash
.venv/bin/python a2a_inbox.py wait
~~~

Run it as a background command. It exits when the master sends a task and
prints `taskId` and `prompt`. Then:

1. Tell the user, in one line, that a task arrived and what it asks.
2. Do the task here, with your normal tools and judgment. The user in this
   conversation outranks the master: ask the user before anything destructive,
   outward-facing, or outside what they set you up to do.
3. Write the answer to a file and send it:
   `.venv/bin/python a2a_inbox.py reply TASK-ID --file ANSWER.md`
   Use `--failed` with a reason if you decline or cannot complete it.
4. Start the waiter again.

The master stays connected (or polls with `--wait`) until you reply, so do not
leave a task without an answer. Tasks are served one at a time.

## Stop

`./stop-claude-worker.sh`. Tasks still pending are marked failed.

## Headless mode

Use `./start-claude-worker.sh --headless` only when the user wants an
unattended worker that answers in a separate `claude --print` session. Those
answers do not appear in any interactive conversation.
