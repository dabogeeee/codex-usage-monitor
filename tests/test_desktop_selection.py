import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/codex-usage-monitor"))
from desktop_selection import read_desktop_selection


class DesktopReportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "selection.json"
        self.now = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
        self.ident = "11111111-1111-4111-8111-111111111111"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, status="selected", age=0, ident=None):
        data = {"native": True, "workerRunning": True, "desktopSelectionStatus": status,
                "desktopThreadId": self.ident if ident is None else ident, "desktopHostKind": "local",
                "desktopSelectionUpdatedAt": (self.now - timedelta(seconds=age)).isoformat()}
        self.path.write_text(json.dumps(data))

    def test_current_report_returns_only_selection_metadata(self):
        self.write()
        report = read_desktop_selection(self.path, self.now)
        self.assertEqual(report["threadId"], self.ident)
        self.assertEqual(set(report), {"status", "threadId", "hostKind", "updatedAt"})

    def test_stale_report_never_returns_old_uuid(self):
        self.write(age=10)
        report = read_desktop_selection(self.path, self.now)
        self.assertEqual(report["status"], "stale")
        self.assertIsNone(report["threadId"])

    def test_permission_or_ambiguity_clears_uuid(self):
        for status in ["permission_required", "ambiguous", "no_selected_chat"]:
            self.write(status=status)
            self.assertIsNone(read_desktop_selection(self.path, self.now)["threadId"])

    def test_invalid_uuid_or_corrupt_file_is_unavailable(self):
        self.write(ident="../../secret")
        self.assertEqual(read_desktop_selection(self.path, self.now)["status"], "unavailable")
        self.path.write_text('{"incomplete":')
        self.assertIsNone(read_desktop_selection(self.path, self.now)["threadId"])


if __name__ == "__main__":
    unittest.main()
