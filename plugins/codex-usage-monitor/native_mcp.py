#!/usr/bin/env python3
"""Native-only Codex usage MCP transport. No web resources or HTTP listener."""
import argparse
import json
import plistlib
import re
import signal
import subprocess
import sys
from pathlib import Path

from usage import Monitor

ROOT = Path(__file__).resolve().parent
VERSION = "0.2.0"


def valid_thread(value):
    return value is None or (isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)))


def open_native_panel():
    for app in (Path.home() / "Applications/CodexUsage.app", ROOT / "native/CodexUsage.app"):
        info = app / "Contents/Info.plist"
        if not info.exists() or plistlib.loads(info.read_bytes()).get("CFBundleIdentifier") != "com.codexusage.monitor":
            continue
        subprocess.run(["/usr/bin/open", "-a", str(app)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"native": True, "applicationPath": str(app),
                "notice": "已打开原生悬浮用量面板。自动启动由本机启动项提供，可在面板或菜单栏选择主题。"}
    raise RuntimeError("尚未安装原生面板，请运行安装原生面板并启用自动打开.command。")


TOOLS = [
    {"name": "get_usage_snapshot", "title": "读取 Codex 用量",
     "description": "读取本机每日/周/月/累计 tokens、账号官方额度及汇总、聊天缓存命中率和最近请求上下文估算。thread_id 省略时使用最近产生用量记录的普通聊天，不能称为桌面端当前选中聊天。官方汇总可能延迟。",
     "inputSchema": {"type": "object", "properties": {"thread_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {"generatedAt": {"type": "string"}, "local": {"type": "object"}, "account": {"type": "object"}}, "required": ["generatedAt", "local", "account"]},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True}},
    {"name": "open_usage_dashboard", "title": "打开 Codex 用量面板",
     "description": "打开 macOS 原生悬浮面板和菜单栏入口。不会打开浏览器，不会返回网页地址。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {"native": {"type": "boolean"}, "applicationPath": {"type": "string"}, "notice": {"type": "string"}}, "required": ["native", "applicationPath", "notice"]},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True}},
]


def mcp_reply(request, monitor):
    ident = request.get("id")
    def error(code, message):
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}
    method = request.get("method")
    if not isinstance(method, str):
        return error(-32600, "Invalid request")
    if "id" not in request:
        return None
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return error(-32602, "Params must be an object")
    if method == "initialize":
        supported = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
        version = params.get("protocolVersion")
        result = {"protocolVersion": version if version in supported else "2025-11-25",
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "codex-usage-monitor", "version": VERSION},
                  "instructions": "Open the native panel with open_usage_dashboard. No browser URL is returned. Official totals may lag; latest activity is not the desktop selection. Context usage is a last-request estimate. Do not publish local usage data."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return error(-32602, "Arguments must be an object")
        if params.get("name") == "get_usage_snapshot":
            thread_id = arguments.get("thread_id")
            if set(arguments) - {"thread_id"} or not valid_thread(thread_id):
                return error(-32602, "Invalid arguments")
            data = monitor.snapshot(thread_id)
        elif params.get("name") == "open_usage_dashboard":
            if arguments:
                return error(-32602, "This tool accepts no arguments")
            data = open_native_panel()
        else:
            return error(-32602, "Unknown tool")
        result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, allow_nan=False)}], "structuredContent": data}
    else:
        return error(-32601, "Method not found")
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def run_mcp(monitor):
    initialized = False
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
            elif "id" in request and not initialized and request.get("method") != "initialize":
                reply = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32000, "message": "Initialize first"}}
            else:
                reply = mcp_reply(request, monitor)
                if request.get("method") == "initialize" and reply and "result" in reply:
                    initialized = True
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        except Exception:
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32603, "message": "Could not read usage; check local Codex setup."}}
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Codex 原生用量面板")
    parser.add_argument("--native", action="store_true", help="打开原生悬浮面板（默认）")
    parser.add_argument("--mcp", action="store_true", help="运行 MCP stdio 服务")
    parser.add_argument("--snapshot", action="store_true", help="输出一次统计 JSON")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--refresh-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.native or not (args.mcp or args.snapshot):
        print(json.dumps(open_native_panel(), ensure_ascii=False))
        return
    monitor = Monitor(args.codex_home, args.timezone, args.refresh_seconds)
    try:
        if args.snapshot:
            monitor.refresh_account()
            print(json.dumps(monitor.snapshot(), ensure_ascii=False, indent=2, allow_nan=False))
        else:
            monitor.start()
            run_mcp(monitor)
    finally:
        monitor.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
