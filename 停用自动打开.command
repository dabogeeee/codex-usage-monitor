#!/bin/zsh
set -e
/bin/launchctl bootout "gui/$(id -u)/com.codexusage.monitor" 2>/dev/null || true
/usr/bin/python3 - <<'PY'
from pathlib import Path
import plistlib
path=Path.home()/'Library/LaunchAgents/com.codexusage.monitor.plist'
if path.exists():
    if plistlib.loads(path.read_bytes()).get('Label')!='com.codexusage.monitor':
        raise SystemExit('启动项标识不匹配，保留文件。')
    path.unlink()
print('已停用自动打开。原生应用保留，可手动启动。')
PY
