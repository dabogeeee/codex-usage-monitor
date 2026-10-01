"""Build the native macOS companion bundled with the Codex plugin."""
import plistlib
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex-usage-monitor"
APP = PLUGIN / "native/CodexUsage.app"


def main():
    executable = APP / "Contents/MacOS/CodexUsage"
    backend = APP / "Contents/Resources/backend"
    executable.parent.mkdir(parents=True, exist_ok=True)
    backend.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".runtime/swift-module-cache"
    cache.mkdir(parents=True, exist_ok=True)
    # Use the CLT default SDK rather than xcrun's newest installed SDK; machines
    # can have a preview SDK whose Swift modules require a newer compiler.
    sdk = os.environ.get("CODEX_USAGE_SWIFT_SDK", "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
    subprocess.run(["/usr/bin/swiftc", "-swift-version", "5", "-parse-as-library", "-O",
                    "-sdk", sdk, "-target", "arm64-apple-macosx14.0", "-module-cache-path", str(cache),
                    str(ROOT / "native/CodexUsage.swift"), str(ROOT / "native/DesktopSelection.swift"), "-o", str(executable)], check=True)
    for name in ("usage.py", "native_bridge.py"):
        shutil.copy2(PLUGIN / name, backend / name)
    info = {"CFBundleIdentifier": "com.codexusage.monitor", "CFBundleName": "CodexUsage",
            "CFBundleDisplayName": "Codex 用量面板", "CFBundleExecutable": "CodexUsage",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.3.0",
            "CFBundleVersion": "3", "LSMinimumSystemVersion": "14.0", "LSUIElement": True,
            "LSMultipleInstancesProhibited": True, "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Local Codex usage companion"}
    (APP / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(APP)], check=True)
    print(str(APP))


if __name__ == "__main__":
    main()
