#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
HOST="$(printenv A2A_HOST 2>/dev/null || true)"
PORT="$(printenv A2A_PORT 2>/dev/null || true)"
CONTEXT_ID="$(printenv A2A_CONTEXT_ID 2>/dev/null || true)"
STATE_DIR="$(printenv A2A_STATE_DIR 2>/dev/null || true)"
PID_FILE="$(printenv A2A_PID_FILE 2>/dev/null || true)"
LOG_FILE="$(printenv A2A_LOG_FILE 2>/dev/null || true)"
BASE_URL="$(printenv A2A_BASE_URL 2>/dev/null || true)"
REPOSITORY_URL="$(printenv A2A_REPOSITORY_URL 2>/dev/null || true)"
if [ -z "$HOST" ] && [ "$(printenv A2A_AUTO_TAILSCALE 2>/dev/null || true)" != "0" ] &&
    command -v tailscale >/dev/null 2>&1; then
    HOST="$(tailscale ip -4 2>/dev/null | head -n 1 | tr -d '[:space:]')"
fi
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8766}"
CONTEXT_ID="${CONTEXT_ID:-claude-worker}"
EXECUTOR="$(printenv A2A_EXECUTOR 2>/dev/null || true)"
EXECUTOR="${EXECUTOR:-auto}"
FOREGROUND=1

usage() {
    cat >&2 <<EOF
Usage: $0 [options] [port] [context-id]

Options:
  --foreground             Run in standby and keep the terminal occupied (default).
  --background             Run detached and return the prompt.
  --host HOST              Interface/IP where the worker listens.
  --port PORT              TCP port (default: 8766).
  --base-url URL           URL used by the remote master.
  --context-id ID          Stable A2A context ID.
  --state-dir DIRECTORY    SQLite, session, and log directory.
  --repository-url URL     Repository URL shared with the master.
  --attached-session UUID  Answer tasks from that Claude Code conversation.
                           mount-worker.sh prints the full command.
  --attached               Answer tasks from the Claude Code conversation that
                           runs this launcher (see CLAUDE.md).
  --headless               Answer tasks with a separate claude --print session.
                           Without either flag, attached is used when launched
                           from Claude Code and headless otherwise.

The port and context ID are also accepted as positional arguments.
EOF
}

positional=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --background)
            FOREGROUND=0
            shift
            ;;
        --foreground)
            FOREGROUND=1
            shift
            ;;
        --host)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            HOST="$2"
            shift 2
            ;;
        --port)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            PORT="$2"
            shift 2
            ;;
        --base-url)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            BASE_URL="$2"
            shift 2
            ;;
        --context-id)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            CONTEXT_ID="$2"
            shift 2
            ;;
        --state-dir)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            STATE_DIR="$2"
            shift 2
            ;;
        --repository-url)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            REPOSITORY_URL="$2"
            shift 2
            ;;
        --attached-session)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            EXECUTOR=attached
            export A2A_ATTACHED_SESSION="$2"
            shift 2
            ;;
        --attached)
            EXECUTOR=attached
            shift
            ;;
        --headless)
            EXECUTOR=headless
            shift
            ;;
        --help|-h)
            usage >&1
            exit 0
            ;;
        --*)
            usage
            exit 2
            ;;
        *)
            positional+=("$1")
            shift
            ;;
    esac
done

if [ "${#positional[@]}" -gt 0 ]; then
    PORT="${positional[0]}"
fi
if [ "${#positional[@]}" -gt 1 ]; then
    CONTEXT_ID="${positional[1]}"
fi
if [ "${#positional[@]}" -gt 2 ]; then
    usage
    exit 2
fi

STATE_DIR="${STATE_DIR:-$ROOT/state}"
if [ -z "$REPOSITORY_URL" ] && command -v git >/dev/null 2>&1; then
    # Drop any credentials embedded in the remote URL before sharing it.
    REPOSITORY_URL="$(git -C "$ROOT" remote get-url origin 2>/dev/null |
        sed -E 's#^(https?://)[^/@]+@#\1#' || true)"
fi
PID_FILE="${PID_FILE:-$STATE_DIR/claude-worker.pid}"
LOG_FILE="${LOG_FILE:-$STATE_DIR/claude-worker.log}"
BASE_URL="${BASE_URL:-http://$HOST:$PORT}"
PYTHON="$(printenv PYTHON 2>/dev/null || true)"
if [ -z "$PYTHON" ]; then
    if [ -x "$ROOT/.venv/bin/python" ]; then
        PYTHON="$ROOT/.venv/bin/python"
    else
        PYTHON=python3
    fi
fi

mkdir -p "$STATE_DIR"
if [ -f "$PID_FILE" ]; then
    existing_pid="$(tr -d '[:space:]' < "$PID_FILE")"
    existing_is_worker=0
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] &&
        kill -0 "$existing_pid" 2>/dev/null &&
        [ -r "/proc/$existing_pid/cmdline" ] &&
        tr '\0' ' ' < "/proc/$existing_pid/cmdline" | grep -Fq "$ROOT/a2a_bridge.py"; then
        existing_is_worker=1
    fi
    if [ "$existing_is_worker" -eq 1 ]; then
        printf 'Claude worker is already running (PID %s).\n' "$existing_pid"
        printf 'Agent Card: %s/.well-known/agent-card.json\n' "$BASE_URL"
        exit 0
    fi
    mv "$PID_FILE" "$PID_FILE.stale.$existing_pid" 2>/dev/null || true
fi

export A2A_STATE_DIR="$STATE_DIR"
export A2A_HOST="$HOST"
export A2A_PORT="$PORT"
export A2A_BASE_URL="$BASE_URL"
export A2A_CONTEXT_ID="$CONTEXT_ID"
export A2A_REPOSITORY_URL="$REPOSITORY_URL"
export A2A_CLAUDE_CWD="$ROOT"
export A2A_EXECUTOR="$EXECUTOR"
if [ "$FOREGROUND" -eq 1 ]; then
    export A2A_WORKER_MODE=standby
else
    export A2A_WORKER_MODE=background
fi
if [ "$FOREGROUND" -eq 1 ]; then
    exec "$PYTHON" "$ROOT/a2a_bridge.py" \
        --host "$HOST" --port "$PORT" --base-url "$BASE_URL" \
        --context-id "$CONTEXT_ID" --cwd "$ROOT" \
        --session-file "$STATE_DIR/claude-session.json" \
        --task-db "$STATE_DIR/tasks.sqlite3"
fi

umask 077
launcher=(nohup)
if command -v setsid >/dev/null 2>&1; then
    # Detach the worker from the invoking shell so it remains available after
    # this launcher returns, including when it is started from a terminal job.
    launcher+=(setsid)
fi

"${launcher[@]}" env PYTHONUNBUFFERED=1 "$PYTHON" "$ROOT/a2a_bridge.py" \
    --host "$HOST" --port "$PORT" --base-url "$BASE_URL" \
    --context-id "$CONTEXT_ID" --cwd "$ROOT" \
    --session-file "$STATE_DIR/claude-session.json" \
    --task-db "$STATE_DIR/tasks.sqlite3" \
    >>"$LOG_FILE" 2>&1 < /dev/null &
worker_pid=$!
printf '%s\n' "$worker_pid" > "$PID_FILE"

ready=0
for _ in $(seq 1 40); do
    if ! kill -0 "$worker_pid" 2>/dev/null; then
        printf 'Claude worker exited during startup. Check %s\n' "$LOG_FILE" >&2
        exit 1
    fi
    if command -v curl >/dev/null 2>&1 &&
        curl -fsS --max-time 1 "$BASE_URL/.well-known/agent-card.json" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.25
done

printf 'Claude worker started in background (PID %s).\n' "$worker_pid"
if [ "$ready" -eq 0 ]; then
    printf 'The process is still active; the Agent Card has not responded yet.\n' >&2
fi

"$PYTHON" "$ROOT/connection_pack.py"
