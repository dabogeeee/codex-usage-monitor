#!/bin/zsh
set -e
TASK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec /usr/bin/python3 "$TASK_DIR/scripts/install_native.py"
