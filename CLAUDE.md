# Instructions for the Claude Code agent that mounts this worker

If a user gives you this repository and asks you to mount it or to act as the
worker ("móntate esto", "móntate como worker"), the worker is **this
conversation**. Every request from the Codex master must arrive here, and the
user expects to watch you receive it and answer it here. Do not start a
separate Claude session, and do not start the listener yourself: the user runs
it.

## Mount

1. Clone the repository if it is not on disk yet, and work from its root.
2. Run `./mount-worker.sh` in the foreground. It creates `.venv/` if needed and
   prints the listener command, which includes this conversation's session
   UUID. Pass listener options after it only if the user asks for them, for
   example `./mount-worker.sh --port 8767`.
3. Give the user that command verbatim and tell them to run it in a separate
   terminal. The listener stays in standby there and prints the copy-ready
   instructions for the Codex master. Those instructions are complete; the
   master needs nothing else.
4. Start the inbox waiter as a background command (the exact path is in the
   output of `mount-worker.sh`):

   ~~~bash
   .venv/bin/python a2a_inbox.py wait
   ~~~

   It can start before the listener; it waits until tasks exist.

## Serve tasks

The waiter exits when the master sends a task and prints `taskId` and
`prompt`. Then:

1. Tell the user, in one line, that a task arrived and what it asks.
2. Do the task here, with your normal tools and judgment. The user in this
   conversation outranks the master: ask the user before anything destructive,
   outward-facing, or outside what they set you up to do.
3. Write the answer to a file and send it:
   `.venv/bin/python a2a_inbox.py reply TASK-ID --file ANSWER.md`
   Use `--failed` with a reason if you decline or cannot complete it.
4. Start the waiter again. Keep exactly one waiter running while the worker is
   mounted.

The master waits until you reply, so do not leave a task without an answer.
Tasks arrive one at a time.

## Stop

The user stops the listener with `Ctrl+C` in its terminal. Tasks still pending
are marked failed. Then stop your waiter.

## Headless mode

`./start-claude-worker.sh --headless` runs an unattended worker that answers in
a separate `claude --print` session. Use it only when the user asks for that;
its answers do not appear in any conversation.
