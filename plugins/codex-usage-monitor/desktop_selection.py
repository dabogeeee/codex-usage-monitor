"""Read the companion's UUID-only selection report, never the desktop UI itself."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

STATUSES = {"selected", "permission_required", "codex_closed", "ambiguous", "ambiguous_windows",
            "window_unavailable", "scan_incomplete", "no_selected_chat"}
UUID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def read_desktop_selection(path=None, now=None):
    path = Path(path) if path else Path.home() / "Library/Application Support/CodexUsage/runtime-status.json"
    empty = {"status": "unavailable", "threadId": None, "hostKind": None, "updatedAt": None}
    try:
        if path.stat().st_size > 65536:
            return empty
        value = json.loads(path.read_text())
        status = value.get("desktopSelectionStatus")
        updated = value.get("desktopSelectionUpdatedAt")
        stamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        current = now or datetime.now(timezone.utc)
        age = (current - stamp).total_seconds()
        if age < -2 or age > 5:
            return dict(empty, status="stale")
        if status not in STATUSES or value.get("native") is not True or not value.get("workerRunning"):
            return empty
        ident, host = value.get("desktopThreadId"), value.get("desktopHostKind")
        if status == "selected":
            if not isinstance(ident, str) or not UUID.fullmatch(ident) or host not in ("local", "remote"):
                return empty
            ident = ident.lower()
        else:
            ident = None; host = None
        return {"status": status, "threadId": ident, "hostKind": host, "updatedAt": updated}
    except (OSError, ValueError, TypeError, AttributeError):
        return empty
