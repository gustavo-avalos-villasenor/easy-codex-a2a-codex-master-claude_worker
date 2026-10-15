# Instructions for the Claude Code agent that mounts this worker

If a user gives you this repository and asks you to mount it or to act as the
worker ("móntate esto", "móntate como worker"), the worker is **this
conversation**. Every request from the Codex master must arrive here, and the
user expects to watch you receive it and answer it here. Do not start a
separate Claude session, and do not start the listener yourself: the user runs
it.

## How it works (read this first)

Three processes must be alive at the same time:

| Process | Who runs it | Where |
| --- | --- | --- |
| Listener (`start-claude-worker.sh --attached-session …`) | The user | A separate terminal |
| This Claude Code conversation | The user | This terminal; **it must stay open** |
| Waiter (`a2a_inbox.py wait`) | You | A background command in this conversation |

The waiter is a background command of this conversation, so **it dies whenever
this conversation is closed, restarted, or resumed** (`claude --resume`,
`claude -c`). When that happens Claude Code tells you that a background
command was "stopped" or "didn't finish before the previous session ended".
Nothing else restarts it: you must.

While no waiter is running, the listener still accepts tasks. Each one fails
after the claim timeout (120 seconds by default, `A2A_ATTACHED_CLAIM_TIMEOUT`)
with a message telling the master that the worker is not listening. The Agent
Card reports `metadata.attachedWorker` as `listening`, `busy`, or
`not listening`, so the master can check before it sends anything.

## Mount

1. Clone the repository if it is not on disk yet, into a permanent directory
   (for example the user's home), not a temporary or scratch directory: the
   listener and the waiter keep their state in `state/` inside it. Work from
   its root.
2. Run `./mount-worker.sh` in the foreground. It creates `.venv/` if needed and
   prints the listener command, which includes this conversation's session
   UUID. Pass listener options after it only if the user asks for them, for
   example `./mount-worker.sh --port 8767`.
3. Give the user that command verbatim and tell them to:
   - run it in a separate terminal on this machine, as the OS user that owns
     the repository directory (the listener writes `state/`);
   - keep this Claude Code conversation open while the worker is mounted.

   The listener stays in standby there and prints the copy-ready instructions
   for the Codex master. It also saves them to `state/connection-pack.txt`, so
   the user does not need to scroll back: point them to that file, or print it
   for them with `.venv/bin/python connection_pack.py`.
4. Start the waiter as a background command (the exact path is in the output
   of `mount-worker.sh`):

   ~~~bash
   .venv/bin/python a2a_inbox.py wait
   ~~~

   It can start before the listener; it waits until tasks exist.
5. Check the result with `.venv/bin/python a2a_inbox.py status`. It must say
   `Waiter: listening`.

## Serve tasks

The waiter exits when the master sends a task and prints `taskId` and
`prompt`. The master now sees the task as received. Then:

1. Tell the user, in one line, that a task arrived and what it asks.
2. Do the task here, with your normal tools and judgment. The user in this
   conversation outranks the master: ask the user before anything destructive,
   outward-facing, or outside what they set you up to do.
3. Write the answer to a file and send it:

   ~~~bash
   .venv/bin/python a2a_inbox.py reply TASK-ID --file ANSWER.md
   ~~~

   To decline it or report that you could not complete it:

   ~~~bash
   .venv/bin/python a2a_inbox.py reply TASK-ID --failed "reason"
   ~~~

4. Start the waiter again. Keep exactly one waiter running while the worker is
   mounted.

The master waits until you reply, so do not leave a task without an answer.
A task that the waiter delivered never expires on its own. Tasks arrive one
at a time.

## Recover

Do this whenever the waiter stopped without delivering a task (a "stopped"
notification, a resumed conversation, or the user reporting that the master
sees no answer):

1. Run `.venv/bin/python a2a_inbox.py status`.
2. If it lists pending tasks, answer each one with `reply` (or `--failed` with
   a reason if it is stale). A pending task is redelivered by the next `wait`.
3. Start the waiter again as a background command.
4. If the status says the task database is missing, the listener is not
   running: ask the user to start it again with the command from
   `mount-worker.sh`.

## Stop

The user stops the listener with `Ctrl+C` in its terminal. Tasks still pending
are marked failed. Then stop your waiter.

## Headless mode

`./start-claude-worker.sh --headless` runs an unattended worker that answers in
a separate `claude --print` session. Use it only when the user asks for that;
its answers do not appear in any conversation.
