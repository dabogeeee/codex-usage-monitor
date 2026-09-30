"""JSONL bridge for the native panel. Uses pipes; starts no HTTP server."""
import json
import queue
import re
import signal
import sys
import threading
import time

from usage import Monitor


def validated_request(value):
    if not isinstance(value, dict) or set(value) - {"thread_id", "request_id"}:
        return None
    thread = value.get("thread_id")
    ident = value.get("request_id", 0)
    if thread is not None and not (isinstance(thread, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread)):
        return None
    if not isinstance(ident, int) or isinstance(ident, bool) or ident < 0:
        return None
    return thread, ident


def main():
    monitor = Monitor()
    incoming = queue.Queue()
    stopped = threading.Event()
    def stop(*_):
        stopped.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def read_requests():
        for line in sys.stdin:
            try:
                request = validated_request(json.loads(line))
                if request is not None:
                    incoming.put(request)
            except ValueError:
                pass
        stopped.set()
    threading.Thread(target=read_requests, daemon=True).start()
    monitor.start()
    thread_id, request_id, next_update = None, 0, 0
    try:
        while not stopped.is_set():
            try:
                thread_id, request_id = incoming.get(timeout=0.2)
                while not incoming.empty():
                    thread_id, request_id = incoming.get_nowait()
                next_update = 0
            except queue.Empty:
                pass
            if time.monotonic() >= next_update:
                snapshot = monitor.snapshot(thread_id)
                snapshot["requestId"] = request_id
                print(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), flush=True)
                next_update = time.monotonic() + 10
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        monitor.close()


if __name__ == "__main__":
    main()
