"""Render the app's own native view in both themes and exercise its lifecycle."""
import json
import subprocess
import time
from pathlib import Path


def stop(process):
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=6)


def main():
    root = Path(__file__).resolve().parents[1]
    app = root / "plugins/codex-usage-monitor/native/CodexUsage.app/Contents/MacOS/CodexUsage"
    output = root / ".runtime"
    output.mkdir(exist_ok=True)
    results = {}
    for theme in ("light", "dark"):
        status = output / ("native-" + theme + ".json")
        picture = output / ("native-" + theme + ".png")
        for path in (status, picture):
            path.unlink(missing_ok=True)
        process = subprocess.Popen([str(app), "--watch", "--theme", theme, "--status", str(status), "--render", str(picture)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 28
            data = {}
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Native app exited before rendering: " + process.stderr.read().decode(errors="replace")[:1000])
                if status.exists():
                    data = json.loads(status.read_text())
                if picture.exists() and data.get("accountStatus") == "ok":
                    break
                time.sleep(0.2)
            assert picture.exists(), theme + " screenshot was not rendered"
            assert data["native"] and data["panelVisible"] and data["codexRunning"] and data["workerRunning"]
            assert data["chatMetricsAvailable"] and data["theme"] == theme
            assert ("Dark" in data["effectiveAppearance"]) == (theme == "dark")
            assert picture.stat().st_size > 20000
            assert data["menuInformationRows"] >= 9
            results[theme] = {"nativeWindow": True, "officialAccount": True, "rendered": True, "themeApplied": True}
        finally:
            stop(process)
    status = output / "native-lifecycle-final.json"
    status.unlink(missing_ok=True)
    process = subprocess.Popen([str(app), "--watch", "--lifecycle-test", "--status", str(status)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        process.wait(timeout=10)
        assert process.returncode == 0
        data = json.loads(status.read_text())
        assert data["lifecyclePassed"]
        assert data["menuQuotaOnlyPassed"] and data["menuDetailsPassed"]
        results["lifecycle"] = {key: data[key] for key in ("showOnCodexLaunch", "hideOnCodexExit", "closeHidesPanel", "menuReopensPanel")}
        results["menuBar"] = {"quotaOnly": data["menuQuotaOnlyPassed"], "details": data["menuDetailsPassed"]}
    finally:
        stop(process)
    print(json.dumps(results))


if __name__ == "__main__":
    main()
