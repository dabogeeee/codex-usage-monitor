#!/usr/bin/env python3
"""Loopback dashboard and a minimal JSONL MCP stdio transport. No external packages."""
import argparse
import hmac
import json
import re
import secrets
import signal
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from usage import Monitor

ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0"
UI_RESOURCE = "ui://codex-usage-monitor/dashboard-v1.html"


def embedded_html():
    html = (ROOT / "web/index.html").read_text()
    css = (ROOT / "web/style.css").read_text()
    js = (ROOT / "web/app.js").read_text().replace("</script", "<\\/script")
    html = html.replace('<link rel="stylesheet" href="/style.css">', "<style>" + css + "</style>")
    html = html.replace('<script src="/app.js" defer></script>', "")
    return html.replace("</body>", "<script>window.__CODEX_USAGE_MCP__=true;</script><script>" + js + "</script></body>")


def valid_thread(value):
    return value is None or (isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)))


class Dashboard:
    def __init__(self, monitor, port=8766):
        self.monitor = monitor
        self.port = port
        self.server = None
        self.token = secrets.token_urlsafe(32)

    def start(self):
        if self.server:
            return self.url
        dashboard = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log capability URLs, account data or chat titles.

            def send_content(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                origin = "http://127.0.0.1:%d" % self.server.server_port
                if self.headers.get("Host") != "127.0.0.1:%d" % self.server.server_port:
                    self.send_content(403, b"Invalid host", "text/plain")
                    return
                if self.headers.get("Origin") not in (None, origin):
                    self.send_content(403, b"Invalid origin", "text/plain")
                    return
                parsed = urlsplit(self.path)
                static = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8")}
                if parsed.path in static:
                    filename, mime = static[parsed.path]
                    self.send_content(200, (ROOT / "web" / filename).read_bytes(), mime)
                    return
                if parsed.path == "/health":
                    self.send_content(200, b'{"service":"codex-usage-monitor","version":"0.1.0"}', "application/json")
                    return
                if parsed.path != "/api/snapshot":
                    self.send_content(404, b"Not found", "text/plain")
                    return
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
                if not hmac.compare_digest(supplied.encode(), dashboard.token.encode()):
                    self.send_content(401, b'{"error":"Open the dashboard using its full launch URL."}', "application/json")
                    return
                thread_id = parse_qs(parsed.query).get("thread", [None])[0]
                if not valid_thread(thread_id):
                    self.send_content(400, b'{"error":"Invalid thread ID"}', "application/json")
                    return
                try:
                    data = json.dumps(dashboard.monitor.snapshot(thread_id), ensure_ascii=False, allow_nan=False).encode()
                    self.send_content(200, data, "application/json; charset=utf-8")
                except (OSError, ValueError):
                    self.send_content(500, b'{"error":"Local usage could not be read."}', "application/json")

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.url

    @property
    def url(self):
        return "http://127.0.0.1:%d/#key=%s" % (self.server.server_port, self.token)

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


TOOLS = [
    {
        "name": "get_usage_snapshot", "title": "读取 Codex 用量",
        "description": "读取本机每日/周/月/累计 tokens、ChatGPT 账号官方额度与延迟汇总，以及指定聊天的缓存命中率和最近请求上下文估算。不读取桌面端选中状态；不传 thread_id 时显示最近产生 token 活动的普通聊天，不能把它称为当前选中聊天。初始化时官方数据可能尚在加载。",
        "inputSchema": {"type": "object", "properties": {"thread_id": {"type": "string", "description": "已知的聊天 UUID；省略则跟随最近活动聊天。", "pattern": "^[A-Za-z0-9_-]{1,128}$"}}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"generatedAt": {"type": "string"}, "local": {"type": "object"}, "account": {"type": "object"}}, "required": ["generatedAt", "local", "account"]},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
        "_meta": {"ui": {"visibility": ["model", "app"]}},
    },
    {
        "name": "open_usage_dashboard", "title": "打开 Codex 用量面板",
        "description": "启动本机自动刷新的用量面板并返回带临时访问密钥的 loopback URL。将返回的 URL 用 Codex open_in_codex 浏览器工具在右侧打开；没有该工具时提供可点击链接。面板不是原生固定侧栏，支持选择聊天或跟随最近活动；进程退出后面板停止。临时密钥仅用于本机面板，不能上传到其他服务。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"url": {"type": "string"}, "notice": {"type": "string"}}, "required": ["url", "notice"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
        "_meta": {"ui": {"resourceUri": UI_RESOURCE, "visibility": ["model", "app"]},
                  "openai/ui": {"entrypoints": [{"type": "thread"}, {"type": "global"}]}},
    },
]


def mcp_reply(request, monitor, dashboard):
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
                  "capabilities": {"tools": {}, "resources": {}}, "serverInfo": {"name": "codex-usage-monitor", "version": VERSION},
                  "instructions": "Use open_usage_dashboard for the side panel. Report remaining quota as a percentage, never as estimated tokens. Official token summaries can lag. Latest activity is not the desktop selected chat. Context usage is an estimate from the last request. Do not publish local data or the dashboard capability URL."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "resources/list":
        result = {"resources": [{"uri": UI_RESOURCE, "name": "Codex 用量面板", "mimeType": "text/html;profile=mcp-app"}]}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": []}
    elif method == "resources/read":
        if params.get("uri") != UI_RESOURCE:
            return error(-32602, "Unknown resource")
        result = {"contents": [{"uri": UI_RESOURCE, "mimeType": "text/html;profile=mcp-app", "text": embedded_html(),
                                "_meta": {"ui": {"prefersBorder": False, "csp": {"connectDomains": [], "resourceDomains": []}}}}]}
    elif method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return error(-32602, "Arguments must be an object")
        if name == "get_usage_snapshot":
            thread_id = arguments.get("thread_id")
            if not valid_thread(thread_id) or set(arguments) - {"thread_id"}:
                return error(-32602, "Invalid arguments")
            data = monitor.snapshot(thread_id)
        elif name == "open_usage_dashboard":
            if arguments:
                return error(-32602, "This tool accepts no arguments")
            data = {"url": dashboard.start(), "notice": "在 Codex 右侧浏览器面板打开此地址。额度每 60 秒刷新，本机统计每 10 秒刷新。"}
        else:
            return error(-32602, "Unknown tool")
        result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, allow_nan=False)}], "structuredContent": data}
    else:
        return error(-32601, "Method not found")
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def run_mcp(monitor, dashboard):
    initialized = False
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
            elif "id" in request and not initialized and request.get("method") != "initialize":
                reply = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32000, "message": "Initialize first"}}
            else:
                reply = mcp_reply(request, monitor, dashboard)
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
    parser = argparse.ArgumentParser(description="Codex 用量面板：本机统计、账号额度、聊天缓存与上下文")
    parser.add_argument("--mcp", action="store_true", help="运行 MCP stdio 服务")
    parser.add_argument("--serve", action="store_true", help="运行本机用量面板（默认）")
    parser.add_argument("--open", action="store_true", help="同时在默认浏览器打开面板")
    parser.add_argument("--snapshot", action="store_true", help="输出一次统计 JSON 并退出")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--codex-home", default=None, help="要读取的 Codex 数据目录，不修改环境变量")
    parser.add_argument("--refresh-seconds", type=int, default=60)
    args = parser.parse_args()
    monitor = Monitor(args.codex_home, args.timezone, args.refresh_seconds)
    dashboard = Dashboard(monitor, args.port)
    try:
        if args.snapshot:
            monitor.refresh_account()
            print(json.dumps(monitor.snapshot(), ensure_ascii=False, indent=2, allow_nan=False))
        else:
            monitor.start()
            if args.mcp:
                run_mcp(monitor, dashboard)
            else:
                url = dashboard.start()
                print("Codex 用量面板：" + url, flush=True)
                print("保持此终端运行。Ctrl+C 停止。", flush=True)
                if args.open:
                    webbrowser.open(url)
                while True:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        dashboard.close()
        monitor.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
