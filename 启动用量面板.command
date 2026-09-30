#!/bin/zsh
set -e
TASK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec /usr/bin/python3 "$TASK_DIR/plugins/codex-usage-monitor/native_mcp.py" --native
