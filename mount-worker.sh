#!/usr/bin/env bash
# Run by the Claude Code agent that is asked to mount this worker. It prepares
# the repository and prints the command the user runs in a separate terminal.
# That listener sends every task from the Codex master to this conversation.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SESSION="$(printenv CLAUDE_CODE_SESSION_ID 2>/dev/null || true)"
if [ -z "$SESSION" ]; then
    printf 'Run this script from the Claude Code conversation that will act as the worker.\n' >&2
    exit 1
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    printf 'Python 3.10 or newer is required.\n' >&2
    exit 1
}
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    python3 -m venv "$ROOT/.venv"
fi

command="cd $(printf '%q' "$ROOT") && ./start-claude-worker.sh --attached-session $SESSION"
for argument in "$@"; do
    command+=" $(printf '%q' "$argument")"
done

cat <<MESSAGE
Worker prepared for Claude Code session $SESSION.

Run this command in a separate terminal on this machine, as the OS user that
owns $ROOT. It stays in standby, prints the instructions for the Codex master,
and saves them to $ROOT/state/connection-pack.txt:

    $command

Keep this Claude Code conversation open while the worker is mounted.

Then start the inbox waiter in this conversation as a background command. It
stops whenever this conversation is closed, restarted, or resumed; start it
again every time (CLAUDE.md, "Recover"):

    $(printf '%q' "$ROOT/.venv/bin/python") $(printf '%q' "$ROOT/a2a_inbox.py") wait

Check it with:

    $(printf '%q' "$ROOT/.venv/bin/python") $(printf '%q' "$ROOT/a2a_inbox.py") status
MESSAGE
