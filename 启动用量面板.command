#!/bin/zsh
set -e
TASK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$TASK_DIR/plugins/codex-usage-monitor/server.py" --serve --open
