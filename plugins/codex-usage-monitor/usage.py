"""Local token accounting and a read-only Codex app-server client (stdlib only)."""
import copy
import hashlib
import json
import math
import os
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def count(value):
    return int(value) if number(value) and value >= 0 else None


def percentage(part, whole):
    if not number(part) or not number(whole) or whole <= 0 or part < 0:
        return None
    return round(max(0, min(100, part / whole * 100)), 2)


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def parsed_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError, TypeError):
        return None


def aggregate_days(days, today):
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    def total(start):
        return sum(tokens for day, tokens in days.items() if start.isoformat() <= day <= today.isoformat())
    return {"today": total(today), "week": total(week), "month": total(month),
            "all": sum(tokens for day, tokens in days.items() if day <= today.isoformat())}


class LogFile:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.signature = None
        self.events = []
        self.meta = {}
        self.model = None
        self.last = None
        self.skipped = 0

    def update(self):
        stat = self.path.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if signature == self.signature:
            return
        if (self.signature and (stat.st_ino != self.signature[0] or stat.st_size < self.offset
                                or stat.st_size == self.signature[1])):
            self.__init__(self.path)
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            while True:
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break  # The writer may still be writing this JSON line. Retry next time.
                self.offset = stream.tell()
                if not any(key in line for key in (b'"token_count"', b'"session_meta"', b'"turn_context"')):
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self.skipped += 1
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                if row.get("type") == "session_meta":
                    self.meta = {key: payload.get(key) for key in ("id", "source", "cwd", "forked_from_id")}
                elif row.get("type") == "turn_context":
                    self.model = payload.get("model")
                elif row.get("type") == "event_msg" and payload.get("type") == "token_count":
                    self.add_usage(row, payload)
        self.signature = signature

    def add_usage(self, row, payload):
        info = payload.get("info")
        stamp = parsed_time(row.get("timestamp"))
        if not isinstance(info, dict) or stamp is None:
            return
        cumulative = info.get("total_token_usage") or {}
        recent = info.get("last_token_usage") or {}
        if not isinstance(cumulative, dict) or not isinstance(recent, dict):
            return
        total = count(cumulative.get("total_tokens"))
        if total is None:
            return
        canonical = json.dumps([row.get("timestamp"), cumulative, recent], sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        self.events.append((fingerprint, stamp, total, count(recent.get("total_tokens")), self.meta.get("id")))
        if self.last is None or stamp >= self.last["stamp"]:
            self.last = {"stamp": stamp, "recent": recent, "cumulative": cumulative,
                         "window": count(info.get("model_context_window")), "model": self.model}


class LocalUsage:
    def __init__(self, home, timezone_name="Asia/Shanghai"):
        self.home = Path(home)
        self.zone = ZoneInfo(timezone_name)
        self.files = {}
        self.lock = threading.Lock()

    def titles(self):
        # Read only metadata. Never expose first_user_message, previews or raw transcripts.
        paths = sorted(self.home.glob("state_*.sqlite"), reverse=True)
        if not paths:
            return {}
        try:
            with sqlite3.connect(paths[0].as_uri() + "?mode=ro", uri=True, timeout=0.1) as db:
                return dict(db.execute("SELECT id, title FROM threads"))
        except (sqlite3.Error, OSError):
            return {}

    def snapshot(self, thread_id=None, now=None):
        with self.lock:
            return self._snapshot(thread_id, now)

    def _snapshot(self, thread_id, now):
        now = now or datetime.now(self.zone)
        today = now.astimezone(self.zone).date()
        paths = set()
        errors = 0
        for root in (self.home / "sessions", self.home / "archived_sessions"):
            if root.is_dir():
                paths.update(root.rglob("*.jsonl"))
        for path in list(self.files):
            if path not in paths:
                del self.files[path]
        for path in sorted(paths):
            record = self.files.setdefault(path, LogFile(path))
            try:
                record.update()
            except OSError:
                errors += 1
        days = defaultdict(int)
        seen = set()
        threads = {}
        titles = self.titles()
        events = sorted((event for record in self.files.values() for event in record.events), key=lambda item: item[1])
        previous_totals = {}
        for key, stamp, total, recent_total, ident in events:
            if key in seen:
                continue
            seen.add(key)
            previous = previous_totals.get(ident)
            if previous is None:
                # Resumed and forked logs can inherit counters without forked_from_id.
                # Count only the first observed request, not the inherited lifetime.
                delta = min(total, recent_total) if recent_total is not None else total
            elif total < previous:
                delta = total  # A real counter reset starts a new segment.
            else:
                delta = total - previous
            previous_totals[ident] = total
            days[stamp.astimezone(self.zone).date().isoformat()] += delta
        for record in self.files.values():
            if not record.last or not record.meta.get("id"):
                continue
            ident = record.meta["id"]
            previous = threads.get(ident)
            if previous and previous["updatedAt"] >= record.last["stamp"].isoformat():
                continue
            last = record.last
            recent, cumulative = last["recent"], last["cumulative"]
            context_tokens = count(recent.get("total_tokens"))
            if context_tokens is None:
                input_n, output_n = count(recent.get("input_tokens")), count(recent.get("output_tokens"))
                if input_n is not None and output_n is not None:
                    context_tokens = input_n + output_n
            source = record.meta.get("source")
            project = Path(record.meta.get("cwd") or "").name or "未命名项目"
            threads[ident] = {
                "id": ident, "title": titles.get(ident) or (project + " · " + ident[:8]),
                "project": project, "model": last["model"],
                "isSubagent": isinstance(source, dict) and "subagent" in source,
                "updatedAt": last["stamp"].isoformat(),
                "cacheHitPercent": percentage(recent.get("cached_input_tokens"), recent.get("input_tokens")),
                "sessionCacheHitPercent": percentage(cumulative.get("cached_input_tokens"), cumulative.get("input_tokens")),
                "inputTokens": count(recent.get("input_tokens")),
                "cachedInputTokens": count(recent.get("cached_input_tokens")),
                "contextTokens": context_tokens, "contextWindow": last["window"],
                "contextUsedPercent": percentage(context_tokens, last["window"]),
                "contextIsEstimate": True,
            }
        ordered = sorted(threads.values(), key=lambda item: item["updatedAt"], reverse=True)
        ordinary = [item for item in ordered if not item["isSubagent"]]
        selected = threads.get(thread_id) if thread_id else next(iter(ordinary), None)
        totals = aggregate_days(days, today)
        return {
            "totals": totals, "daily": [{"date": day, "tokens": tokens} for day, tokens in sorted(days.items())],
            "threads": ordinary, "selectedThread": selected,
            "selectionMode": "manual" if thread_id else "latest_activity",
            "timezone": str(self.zone), "periodDate": today.isoformat(),
            "fileCount": len(paths), "skippedLines": sum(r.skipped for r in self.files.values()),
            "readErrors": errors, "firstDate": min(days) if days else None,
            "scope": "本机可读取的 Codex 会话记录，含归档与子代理；其他设备及已删除记录不包含在内。",
        }


def codex_binary():
    configured = os.environ.get("CODEX_USAGE_CLI")
    if configured:
        return configured
    resolved = shutil.which("codex")
    if resolved:
        return resolved
    for app in ("ChatGPT", "Codex"):
        root = Path("/Applications") / (app + ".app") / "Contents/Resources"
        for relative in ("codex-cli/CodexCLI.app/Contents/MacOS/codex", "codex"):
            candidate = root / relative
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError("未找到 Codex CLI，请设置 CODEX_USAGE_CLI 为可执行文件路径。")


class RpcError(RuntimeError):
    pass


class CodexRpc:
    ALLOWED = {"initialize", "account/read", "account/rateLimits/read", "account/usage/read"}

    def __init__(self):
        self.proc = None
        self.messages = None
        self.next_id = 0

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        self.close()
        # Disable this plugin only for the observer subprocess, preventing recursive
        # loading if a host eagerly starts plugin MCP servers. No saved config changes.
        command = [codex_binary(), "-c", 'plugins."codex-usage-monitor@codex-usage-local".enabled=false',
                   "app-server", "--stdio"]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.messages = queue.Queue()
        def reader(proc, messages):
            try:
                for line in proc.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        pass
            finally:
                messages.put(None)
        threading.Thread(target=reader, args=(self.proc, self.messages), daemon=True).start()
        self.call("initialize", {"clientInfo": {"name": "codex_usage_monitor", "title": "Codex 用量面板", "version": "0.2.0"},
                                 "capabilities": {"experimentalApi": True}})
        self.proc.stdin.write('{"method":"initialized"}\n')
        self.proc.stdin.flush()

    def call(self, method, params=None):
        if method not in self.ALLOWED:
            raise RpcError("此客户端仅允许读取账号用量。")
        self.next_id += 1
        ident = self.next_id
        try:
            self.proc.stdin.write(json.dumps({"id": ident, "method": method, "params": params or {}}) + "\n")
            self.proc.stdin.flush()
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                value = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
                if value is None:
                    raise RpcError("Codex 服务未启动成功；请检查 CLI 是否可运行及本地状态目录权限。")
                if not isinstance(value, dict):
                    continue
                if value.get("id") != ident:
                    if "id" in value and "method" in value:
                        # Reject any server-initiated action; this observer never grants approvals.
                        self.proc.stdin.write(json.dumps({"id": value["id"], "error": {"code": -32601, "message": "Read-only observer"}}) + "\n")
                        self.proc.stdin.flush()
                    continue
                if "error" in value:
                    code = (value.get("error") or {}).get("code")
                    raise RpcError("官方接口暂时不可用（错误码 %s）。" % code)
                return value.get("result") or {}
        except queue.Empty:
            self.close()  # A late reply must not corrupt the following request.
            raise RpcError("官方接口请求超时，稍后将自动重试。")
        except (BrokenPipeError, OSError, AttributeError):
            self.close()
            raise RpcError("Codex 服务连接已断开，稍后将自动重试。")
        self.close()
        raise RpcError("官方接口请求超时，稍后将自动重试。")

    def close(self):
        if self.proc:
            proc = self.proc
            self.proc = None
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    stream.close()


def quota_bucket(raw, default_plan=None):
    raw = raw if isinstance(raw, dict) else {}
    plan = raw.get("planType") or default_plan
    windows = []
    for slot in ("primary", "secondary"):
        window = raw.get(slot)
        if not isinstance(window, dict):
            continue
        duration = count(window.get("windowDurationMins"))
        used = window.get("usedPercent")
        remaining = round(max(0, min(100, 100 - used)), 2) if number(used) else None
        label = {300: "5 小时", 10080: "一周"}.get(duration, "%s 分钟" % duration if duration else "额度窗口")
        windows.append({"label": label, "durationMinutes": duration, "usedPercent": used if number(used) else None,
                        "remainingPercent": remaining, "resetsAt": count(window.get("resetsAt")),
                        "visibleByDefault": not (str(plan).lower() in ("pro", "prolite", "promax") and duration != 10080)})
    credits = raw.get("credits")
    safe_credits = None
    if isinstance(credits, dict):
        safe_credits = {key: credits.get(key) for key in ("hasCredits", "unlimited", "balance")}
    return {"id": raw.get("limitId") or "codex", "name": raw.get("limitName") or raw.get("limitId") or "Codex",
            "planType": plan, "windows": windows, "credits": safe_credits}


def official_usage(raw, today):
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    lifetime = count(summary.get("lifetimeTokens"))
    buckets = raw.get("dailyUsageBuckets")
    days = {}
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            try:
                day = date.fromisoformat(bucket["startDate"]).isoformat()
            except (ValueError, KeyError, TypeError):
                continue
            tokens = count(bucket.get("tokens"))
            if tokens is not None:
                days[day] = days.get(day, 0) + tokens
    totals = aggregate_days(days, today) if isinstance(buckets, list) else dict.fromkeys(("today", "week", "month", "all"))
    totals["all"] = lifetime
    # Missing current-date buckets can mean delayed reporting, not zero usage.
    if today.isoformat() not in days:
        totals["today"] = None
    return {"totals": totals, "daily": [{"date": day, "tokens": n} for day, n in sorted(days.items())],
            "latestDate": max(days) if days else None, "firstDate": min(days) if days else None,
            "dailyAvailable": isinstance(buckets, list),
            "scope": "账号官方 token 活动汇总；可能延迟。周/月为接口返回日桶之和，服务未说明日桶时区及历史覆盖范围。"}


class Monitor:
    def __init__(self, home=None, timezone_name="Asia/Shanghai", refresh_seconds=60, rpc=None):
        self.local = LocalUsage(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex", timezone_name)
        self.rpc = rpc or CodexRpc()
        self.refresh_seconds = max(15, refresh_seconds)
        self.lock = threading.Lock()
        self.account = {"status": "loading", "planType": None, "authType": None, "buckets": [], "usage": None,
                        "updatedAt": None, "error": None, "usageError": None}
        self.last_attempt = 0
        self.thread = None
        self.stopping = threading.Event()

    def refresh_account(self):
        with self.lock:
            self.last_attempt = time.monotonic()
        try:
            self.rpc.start()
            auth = self.rpc.call("account/read", {"refreshToken": False}).get("account") or {}
            plan, auth_type = auth.get("planType"), auth.get("type")
            if auth_type not in ("chatgpt", "chatgptAuthTokens", "agentIdentity", "personalAccessToken"):
                with self.lock:
                    self.account = {"status": "unavailable", "planType": plan, "authType": auth_type,
                                    "buckets": [], "usage": None, "updatedAt": iso_now(),
                                    "error": "请使用 ChatGPT 账号登录 Codex；API Key 账号没有订阅额度百分比。", "usageError": None}
                return
            limits = self.rpc.call("account/rateLimits/read")
            raw_buckets = limits.get("rateLimitsByLimitId")
            if not isinstance(raw_buckets, dict) or not raw_buckets:
                raw_buckets = {"codex": limits.get("rateLimits") or {}}
            buckets = [quota_bucket(raw, plan) for raw in raw_buckets.values() if isinstance(raw, dict)]
            if not plan:
                plan = next((bucket["planType"] for bucket in buckets if bucket["planType"]), None)
            usage, usage_error = None, None
            try:
                usage = official_usage(self.rpc.call("account/usage/read"), datetime.now(self.local.zone).date())
            except RpcError as error:
                usage_error = str(error)
            with self.lock:
                self.account = {"status": "ok", "planType": plan, "authType": auth_type, "buckets": buckets,
                                "usage": usage, "updatedAt": iso_now(), "error": None, "usageError": usage_error}
        except (RpcError, RuntimeError, OSError) as error:
            with self.lock:
                self.account["status"] = "stale" if self.account["updatedAt"] else "unavailable"
                self.account["error"] = str(error) if isinstance(error, (RpcError, RuntimeError)) else "无法启动 Codex CLI。"

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            def loop():
                while not self.stopping.is_set():
                    self.refresh_account()
                    self.stopping.wait(self.refresh_seconds)
            self.thread = threading.Thread(target=loop, daemon=True)
            self.thread.start()

    def snapshot(self, thread_id=None):
        local = self.local.snapshot(thread_id)
        with self.lock:
            account = copy.deepcopy(self.account)
        return {"generatedAt": iso_now(), "local": local, "account": account,
                "refreshSeconds": self.refresh_seconds, "uiPollSeconds": 10,
                "selectionNotice": "最近活动模式仅跟随最新产生 token 记录的普通聊天，无法读取桌面端当前选中聊天；切换到无新活动的聊天请在面板手动选择。"}

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.rpc.close()
