"""Exercise the installed package over MCP stdio without invoking a model."""
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen


def main():
    root = Path.home() / ".codex/plugins/cache/codex-usage-local/codex-usage-monitor/0.1.0"
    config = json.loads((root / "mcp.json").read_text())["mcpServers"]["usage-monitor"]
    proc = subprocess.Popen([config["command"], *config["args"]], cwd=root / config["cwd"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    messages = queue.Queue()
    def reader():
        for line in proc.stdout:
            messages.put(json.loads(line))
        messages.put(None)
    threading.Thread(target=reader, daemon=True).start()
    counter = 0
    def request(method, params):
        nonlocal counter
        counter += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter, "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            item = messages.get(timeout=15)
            if item is None:
                raise RuntimeError("Installed MCP process closed unexpectedly")
            if item.get("id") == counter:
                return item
        raise TimeoutError(method)
    try:
        first = request("tools/list", {})
        assert first["error"]["code"] == -32000
        result = request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "usage-test", "version": "1.0"}})
        assert result["result"]["serverInfo"]["name"] == "codex-usage-monitor"
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        assert len(request("tools/list", {})["result"]["tools"]) == 2
        uri = request("resources/list", {})["result"]["resources"][0]["uri"]
        assert "window.__CODEX_USAGE_MCP__=true" in request("resources/read", {"uri": uri})["result"]["contents"][0]["text"]
        opened = request("tools/call", {"name": "open_usage_dashboard", "arguments": {}})["result"]["structuredContent"]
        parsed = urlsplit(opened["url"])
        key = parse_qs(parsed.fragment)["key"][0]
        base = parsed.scheme + "://" + parsed.netloc
        with urlopen(Request(base + "/api/snapshot", headers={"Authorization": "Bearer " + key})) as response:
            data = json.load(response)
        deadline = time.monotonic() + 25
        while data["account"]["status"] == "loading" and time.monotonic() < deadline:
            time.sleep(0.25)
            data = request("tools/call", {"name": "get_usage_snapshot", "arguments": {}})["result"]["structuredContent"]
        assert data["account"]["status"] == "ok", data["account"].get("error")
        assert data["account"]["buckets"]
        assert data["local"]["selectedThread"]["contextWindow"] > 0
        assert data["local"]["totals"]["today"] > 0
        print(json.dumps({"installedMcp": True, "authPlan": data["account"]["planType"],
                          "officialUsageAvailable": data["account"]["usage"] is not None,
                          "panelHttp": True, "uiResource": True, "localChatMetrics": True}))
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)
        errors = proc.stderr.read()
        if errors:
            raise RuntimeError("MCP emitted unexpected stderr")


if __name__ == "__main__":
    main()
