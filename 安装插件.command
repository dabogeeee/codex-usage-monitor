#!/bin/zsh
set -e
TASK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
CODEX_USAGE_CLI="${CODEX_USAGE_CLI:-$(command -v codex || true)}"
if [[ -z "$CODEX_USAGE_CLI" ]]; then
  CODEX_USAGE_CLI="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
fi
"$CODEX_USAGE_CLI" plugin marketplace add "$TASK_DIR"
"$CODEX_USAGE_CLI" plugin add codex-usage-monitor@codex-usage-local
/usr/bin/python3 "$TASK_DIR/scripts/install_native.py"
print '安装完成。原生面板将随 Codex 启动自动打开。'
