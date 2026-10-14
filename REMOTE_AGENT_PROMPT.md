# Message for the Codex master

The remote worker is Claude Code, exposed through A2A 1.0 over HTTP. Codex is
the master: read the Agent Card, use the endpoint and `contextId` advertised by
that card, and send requests to the worker.

Replace `WORKER_BASE_URL` with the remote URL printed by the launcher. Do not
use `127.0.0.1` unless the master and worker run on the same machine.

~~~bash
curl -fsS "WORKER_BASE_URL/.well-known/agent-card.json"
~~~

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

