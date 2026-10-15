# Message for the Codex master

The remote worker is Claude Code, exposed through A2A 1.0 over HTTP. Codex is
the master: read the Agent Card, use the endpoint and `contextId` advertised by
that card, and send requests to the worker.

Replace `WORKER_BASE_URL` with the remote URL printed by the launcher. Do not
use `127.0.0.1` unless the master and worker run on the same machine.

~~~bash
curl -fsS "WORKER_BASE_URL/.well-known/agent-card.json"
~~~

The listener prints the exact version of this message, with the real URLs
filled in, and saves it to `state/connection-pack.txt` on the worker host.
Prefer that copy.

If the card has `metadata.attachedWorker`, the worker is a live Claude Code
conversation that a person is watching. Send tasks only while it is
`listening` (or `busy`, which means your task will queue behind the current
one). If it is `not listening`, do not send anything: tell your human that
the worker conversation is not listening and wait until the card says
`listening`.

The worker keeps one persistent Claude session and processes one request at a
time. Later messages preserve that context within the Claude session. Copy the
`contextId` from the card; do not invent one.

For a short request, keep an SSE connection open until the result arrives:

~~~bash
python3 a2a_client.py \
  "Your message for Claude" \
  "WORKER_BASE_URL"
~~~

For a long task, submit the turn and keep the returned `taskId`:

~~~bash
python3 a2a_client.py --submit \
  "Your message for Claude" \
  "WORKER_BASE_URL"

python3 a2a_client.py --wait "RETURNED-TASK-ID" "WORKER_BASE_URL"
~~~

If this master is a Codex CLI conversation and you have its UUID, use
`--submit-and-resume`. The local watcher resumes the conversation once Claude
finishes:

~~~bash
python3 a2a_client.py --submit-and-resume \
  "Your long task for Claude" \
  "WORKER_BASE_URL" \
  "CODEX-CONVERSATION-UUID"
~~~

After using `--submit-and-resume`, do not run `--wait` or submit the same
message again. If the connection drops before the result arrives, keep the
`taskId` and reconnect with `--wait`; do not resubmit automatically because
Claude may already have received and completed the task.

## Task states with a live worker conversation

Read `result.task.status.state` and the status message text:

| State | Message starts with | Meaning and what to do |
| --- | --- | --- |
| `TASK_STATE_SUBMITTED` | | Accepted and queued behind an earlier task. Wait. |
| `TASK_STATE_WORKING` | `Queued: waiting for the worker Claude Code conversation` | Not delivered yet. If nobody receives it within the claim timeout (120 seconds by default) it fails on its own. Do not resend before that. |
| `TASK_STATE_WORKING` | `Received by the worker Claude Code conversation` | Claude has it and is working. A person may be reviewing it, so it can take many minutes. Keep waiting; never resend. |
| `TASK_STATE_COMPLETED` | | The message text is Claude's answer. |
| `TASK_STATE_FAILED` | `The worker Claude Code conversation is not listening` | The task was **not** executed. Tell your human, wait until the Agent Card says `listening`, then resend. |
| `TASK_STATE_FAILED` | anything else | The worker declined the task or could not finish it. Read the reason before sending anything new. |
