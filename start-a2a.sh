#!/usr/bin/env bash
set -euo pipefail

# Compatibility name. The active worker is Claude Code.
exec "$(cd "$(dirname "$0")" && pwd)/start-claude-worker.sh" "$@"
