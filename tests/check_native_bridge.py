"""Verify that the native JSONL stream switches chats and shuts down on EOF."""
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1] / "plugins/codex-usage-monitor"
    process = subprocess.Popen([sys.executable, str(root / "native_bridge.py")], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    messages = queue.Queue()
    def reader():
        for line in process.stdout:
            messages.put(json.loads(line))
        messages.put(None)
    threading.Thread(target=reader, daemon=True).start()
    try:
        initial = messages.get(timeout=15)
        assert initial and initial["local"]["threads"]
        thread = initial["local"]["threads"][-1]["id"]
        process.stdin.write(json.dumps({"thread_id": thread, "request_id": 42}) + "\n")
        process.stdin.flush()
        while True:
            value = messages.get(timeout=15)
            assert value is not None
            if value["requestId"] == 42:
                break
        assert value["local"]["selectedThread"]["id"] == thread
        assert value["local"]["selectionMode"] == "manual"
        process.stdin.write('{"thread_id":null,"request_id":43}\n')
        process.stdin.flush()
        while True:
            value = messages.get(timeout=15)
            if value["requestId"] == 43:
                break
        assert value["local"]["selectionMode"] == "latest_activity"
        process.stdin.close()
        process.wait(timeout=8)
        assert process.returncode == 0
        assert not process.stderr.read()
        print(json.dumps({"nativePipe": True, "chatSwitch": True, "latestActivity": True, "shutdownOnEOF": True}))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
