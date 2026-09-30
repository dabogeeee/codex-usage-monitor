#!/bin/zsh
set -e
TASK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
CODEX_USAGE_CLI="${CODEX_USAGE_CLI:-$(command -v codex || true)}"
if [[ -z "$CODEX_USAGE_CLI" ]]; then
  CODEX_USAGE_CLI="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
fi
"$CODEX_USAGE_CLI" plugin marketplace add "$TASK_DIR"
"$CODEX_USAGE_CLI" plugin add codex-usage-monitor@codex-usage-local
print '安装完成。请打开新聊天并输入：打开 Codex 用量面板。'
