"""Compatibility entry point kept for older launch commands.

The active worker is Claude Code. Codex is the master/client side.
"""

from a2a_bridge_claude import main


if __name__ == "__main__":
    main()
