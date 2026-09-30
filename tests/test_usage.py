import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/codex-usage-monitor"))
from usage import LocalUsage, Monitor, RpcError, official_usage, percentage, quota_bucket
from server import Dashboard, UI_RESOURCE, mcp_reply


def meta(ident="chat-a", source="vscode", fork=None):
    return {"type": "session_meta", "payload": {"id": ident, "source": source, "cwd": "/tmp/project", "forked_from_id": fork}}


def event(stamp, total, inputs=None, cached=0, recent=100, window=1000):
    return {"timestamp": stamp, "type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"total_tokens": total, "input_tokens": total - 10, "cached_input_tokens": cached},
        "last_token_usage": {"total_tokens": recent, "input_tokens": inputs if inputs is not None else recent - 10, "cached_input_tokens": cached},
        "model_context_window": window}}}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class LogsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "sessions" / "test.jsonl"
        self.now = datetime(2026, 9, 30, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.local = LocalUsage(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def read(self, selected=None):
        return self.local.snapshot(selected, self.now)

    def test_cumulative_deltas_and_timezone(self):
        write(self.path, [meta(), event("2026-09-29T15:59:00Z", 100), event("2026-09-29T16:01:00Z", 180),
                          event("2026-09-30T10:00:00Z", 180), event("2026-09-30T11:00:00Z", 230)])
        data = self.read()
        self.assertEqual(data["totals"], {"today": 130, "week": 230, "month": 230, "all": 230})
        self.assertEqual(data["daily"][0], {"date": "2026-09-29", "tokens": 100})

    def test_duplicate_archive_and_fork_history_not_double_counted(self):
        rows = [meta(), event("2026-09-30T09:00:00Z", 100), event("2026-09-30T10:00:00Z", 180)]
        write(self.path, rows)
        write(self.root / "archived_sessions" / "copy.jsonl", rows)
        write(self.root / "sessions" / "fork.jsonl", rows + [meta("chat-b", fork="chat-a"), event("2026-09-30T11:00:00Z", 210, recent=30)])
        self.assertEqual(self.read()["totals"]["all"], 210)

    def test_fork_without_history_uses_last_usage(self):
        write(self.path, [meta("chat-b", fork="chat-a"), event("2026-09-30T09:00:00Z", 10000, recent=80)])
        self.assertEqual(self.read()["totals"]["all"], 80)

    def test_inherited_counters_without_fork_metadata(self):
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 900000, recent=80)])
        self.assertEqual(self.read()["totals"]["all"], 80)

    def test_multiple_files_same_session_reemitted_counter_not_counted_again(self):
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 100)])
        write(self.root / "sessions" / "resume.jsonl", [meta(), event("2026-09-30T10:00:00Z", 100), event("2026-09-30T11:00:00Z", 180)])
        self.assertEqual(self.read()["totals"]["all"], 180)

    def test_metrics_use_last_request_not_lifetime(self):
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 900000, inputs=400, cached=300, recent=500, window=2000)])
        thread = self.read()["selectedThread"]
        self.assertEqual(thread["cacheHitPercent"], 75)
        self.assertEqual(thread["contextUsedPercent"], 25)

    def test_partial_line_is_retried_and_appended_delta_only_once(self):
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 100)])
        line = json.dumps(event("2026-09-30T10:00:00Z", 160))
        with self.path.open("a") as stream:
            stream.write(line[:20])
        self.assertEqual(self.read()["totals"]["all"], 100)
        with self.path.open("a") as stream:
            stream.write(line[20:] + "\n")
        self.assertEqual(self.read()["totals"]["all"], 160)
        self.assertEqual(self.read()["totals"]["all"], 160)

    def test_truncated_file_rebuilt(self):
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 100), event("2026-09-30T10:00:00Z", 200)])
        self.assertEqual(self.read()["totals"]["all"], 200)
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 80)])
        self.assertEqual(self.read()["totals"]["all"], 80)

    def test_missing_null_and_zero_metrics(self):
        self.assertIsNone(percentage(0, 0))
        self.assertEqual(percentage(0, 400), 0)
        self.assertIsNone(percentage(None, 400))
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 100, window=None)])
        self.assertIsNone(self.read()["selectedThread"]["contextUsedPercent"])

    def test_auto_excludes_guardian_but_total_includes_subagent(self):
        write(self.path, [meta(), event("2026-09-30T09:00:00Z", 100)])
        write(self.root / "sessions" / "agent.jsonl", [meta("guard", {"subagent": {"other": "guardian"}}), event("2026-09-30T10:00:00Z", 50)])
        data = self.read()
        self.assertEqual(data["selectedThread"]["id"], "chat-a")
        self.assertEqual(data["totals"]["all"], 150)
        self.assertIsNone(self.read("missing")["selectedThread"])
        self.assertEqual(self.read("guard")["selectedThread"]["id"], "guard")


class OfficialTest(unittest.TestCase):
    def test_plus_pro_windows_by_duration_not_slot(self):
        raw = {"primary": {"usedPercent": 51, "windowDurationMins": 10080}, "secondary": {"usedPercent": 0, "windowDurationMins": 300}}
        plus = quota_bucket(raw, "plus")["windows"]
        pro = quota_bucket(raw, "pro")["windows"]
        self.assertEqual([w["remainingPercent"] for w in plus], [49, 100])
        self.assertEqual([w["visibleByDefault"] for w in plus], [True, True])
        self.assertEqual([w["visibleByDefault"] for w in pro], [True, False])

    def test_missing_percent_is_not_full_balance(self):
        raw = {"primary": {"usedPercent": None, "windowDurationMins": 300}}
        self.assertIsNone(quota_bucket(raw)["windows"][0]["remainingPercent"])
        self.assertEqual(quota_bucket({"primary": {"usedPercent": 120}})["windows"][0]["remainingPercent"], 0)

    def test_official_today_missing_is_unknown_not_zero(self):
        raw = {"summary": {"lifetimeTokens": 1000}, "dailyUsageBuckets": [{"startDate": "2026-09-28", "tokens": 100}, {"startDate": "2026-09-29", "tokens": 200}]}
        data = official_usage(raw, self.today())
        self.assertEqual(data["totals"], {"today": None, "week": 300, "month": 300, "all": 1000})
        self.assertEqual(data["latestDate"], "2026-09-29")

    def test_official_null_daily_preserves_lifetime_only(self):
        data = official_usage({"summary": {"lifetimeTokens": 1000}, "dailyUsageBuckets": None}, self.today())
        self.assertEqual(data["totals"], {"today": None, "week": None, "month": None, "all": 1000})

    def today(self):
        return datetime(2026, 9, 30).date()


class FakeMonitor:
    def snapshot(self, thread_id=None):
        return {"generatedAt": "now", "local": {"thread": thread_id}, "account": {}}


class TransportTest(unittest.TestCase):
    def test_embedded_resource_is_self_contained(self):
        reply = mcp_reply({"id": 1, "method": "resources/read", "params": {"uri": UI_RESOURCE}}, FakeMonitor(), None)
        html = reply["result"]["contents"][0]["text"]
        self.assertIn("window.__CODEX_USAGE_MCP__=true", html)
        self.assertNotIn('src="/app.js"', html)
        self.assertNotIn('href="/style.css"', html)

    def test_mcp_schema_and_unknown_arguments(self):
        monitor = FakeMonitor()
        data = mcp_reply({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, monitor, None)
        self.assertEqual(data["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(mcp_reply({"method": "notifications/initialized"}, monitor, None))
        data = mcp_reply({"id": 2, "method": "tools/call", "params": {"name": "get_usage_snapshot", "arguments": {"thread_id": "x"}}}, monitor, None)
        self.assertEqual(data["result"]["structuredContent"]["local"]["thread"], "x")
        invalid = mcp_reply({"id": 2, "method": "tools/call", "params": {"name": "get_usage_snapshot", "arguments": {"thread_id": "../../secret"}}}, monitor, None)
        self.assertEqual(invalid["error"]["code"], -32602)

    def test_http_requires_capability_and_rejects_other_origins_and_hosts(self):
        dashboard = Dashboard(FakeMonitor(), 0)
        try:
            url = dashboard.start().split("/#")[0]
            with urlopen(url + "/health") as response:
                self.assertEqual(response.status, 200)
            with self.assertRaises(HTTPError) as caught:
                urlopen(url + "/api/snapshot")
            self.assertEqual(caught.exception.code, 401)
            headers = {"Authorization": "Bearer " + dashboard.token}
            with urlopen(Request(url + "/api/snapshot?thread=x", headers=headers)) as response:
                self.assertEqual(json.load(response)["local"]["thread"], "x")
            for extra in ({"Origin": "https://example.com"}, {"Host": "attacker.example"}):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url + "/api/snapshot", headers=dict(headers, **extra)))
                self.assertEqual(caught.exception.code, 403)
        finally:
            dashboard.close()


if __name__ == "__main__":
    unittest.main()
