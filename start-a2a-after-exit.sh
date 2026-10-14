#!/usr/bin/env bash
set -euo pipefail

# Compatibility name. Codex is now the master/client, so this starts Claude.
exec "$(cd "$(dirname "$0")" && pwd)/start-claude-worker.sh" "$@"
