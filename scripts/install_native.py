"""Install only this native companion and its per-user launch agent."""
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.codexusage.monitor"


def main():
    source = ROOT / "plugins/codex-usage-monitor/native/CodexUsage.app"
    if not (source / "Contents/MacOS/CodexUsage").exists():
        subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/build_native.py")], check=True)
    home = Path.home()
    app = home / "Applications/CodexUsage.app"
    agent = home / "Library/LaunchAgents/com.codexusage.monitor.plist"
    logs = home / "Library/Application Support/CodexUsage"
    if app.exists():
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != LABEL:
            raise RuntimeError("同名应用不是本插件，停止安装以保留原文件。")
    if agent.exists():
        info = plistlib.loads(agent.read_bytes())
        if info.get("Label") != LABEL:
            raise RuntimeError("同名启动项不是本插件，停止安装以保留原文件。")
    app.parent.mkdir(parents=True, exist_ok=True)
    agent.parent.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    domain = "gui/%d" % os.getuid()
    subprocess.run(["/bin/launchctl", "bootout", domain + "/" + LABEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if app.exists():
        backup = app.with_name("CodexUsage.previous.app")
        if backup.exists():
            backup_info = plistlib.loads((backup / "Contents/Info.plist").read_bytes())
            if backup_info.get("CFBundleIdentifier") != LABEL:
                raise RuntimeError("同名备份不是本插件，停止安装。")
            shutil.rmtree(backup)
        app.rename(backup)
    shutil.copytree(source, app)
    configuration = {"Label": LABEL, "ProgramArguments": [str(app / "Contents/MacOS/CodexUsage"), "--watch", "--status", str(logs / "runtime-status.json")],
                     "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Interactive",
                     "LimitLoadToSessionType": "Aqua", "StandardOutPath": str(logs / "native.log"),
                     "StandardErrorPath": str(logs / "native-error.log")}
    agent.write_bytes(plistlib.dumps(configuration))
    os.chmod(agent, 0o644)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(agent)], check=True)
    print("已安装原生用量面板：" + str(app))
    print("已启用随 Codex 启动自动打开：" + str(agent))


if __name__ == "__main__":
    main()
