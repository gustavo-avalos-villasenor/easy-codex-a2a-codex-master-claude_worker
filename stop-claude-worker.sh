#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="$(printenv A2A_STATE_DIR 2>/dev/null || true)"
STATE_DIR="${STATE_DIR:-$ROOT/state}"
PID_FILE="$(printenv A2A_PID_FILE 2>/dev/null || true)"
PID_FILE="${PID_FILE:-$STATE_DIR/claude-worker.pid}"

if [ ! -f "$PID_FILE" ]; then
    printf 'No Claude worker PID found at %s.\n' "$PID_FILE"
    exit 0
fi

worker_pid="$(tr -d '[:space:]' < "$PID_FILE")"
if ! [[ "$worker_pid" =~ ^[0-9]+$ ]]; then
    printf 'Invalid PID in %s.\n' "$PID_FILE" >&2
    exit 1
fi

if ! kill -0 "$worker_pid" 2>/dev/null; then
    printf 'PID %s is no longer active.\n' "$worker_pid"
    mv "$PID_FILE" "$PID_FILE.stale.$worker_pid"
    exit 0
fi

if [ -r "/proc/$worker_pid/cmdline" ] &&
    ! tr '\0' ' ' < "/proc/$worker_pid/cmdline" | grep -Fq "$ROOT/a2a_bridge.py"; then
    printf 'PID %s does not appear to be the Claude worker; leaving it untouched.\n' "$worker_pid" >&2
    exit 1
fi

kill "$worker_pid"
for _ in $(seq 1 40); do
    if ! kill -0 "$worker_pid" 2>/dev/null; then
        mv "$PID_FILE" "$PID_FILE.stopped.$worker_pid"
        printf 'Claude worker stopped.\n'
        exit 0
    fi
    sleep 0.25
done

printf 'Signal sent to PID %s, but it is still running.\n' "$worker_pid" >&2
exit 1
