"""Real HTTP/HMAC bridge fixture; does not import or imitate Autodesk APIs."""

import json
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from CodexFusionInterop.bridge import Bridge


events = queue.Queue()
stopping = threading.Event()
calls = []
mode = os.environ.get("FUSION_ADDIN_FIXTURE_MODE", "normal")


def dispatch(request):
    if threading.current_thread() is not threading.main_thread():
        raise AssertionError("Fixture executed outside its main thread")
    calls.append(request["request_id"])
    if request["args"].get("delay"):
        time.sleep(float(request["args"]["delay"]))
    if request["args"].get("failure"):
        return {"ok": False, "error": {"code": "FIXTURE_REJECTION", "message": "bounded fixture failure", "outcome": request["args"]["failure"]}}
    return {"ok": True, "data": {"calls": len(calls), "args": request["args"], "thread": "main"}, "state": "fixture-after"}


def fire_event(key):
    if mode != "no_events":
        events.put(key)
    return True


bridge = Bridge(dispatch, "f" * 64, fire_event, os.environ["FUSION_ADDIN_FIXTURE_TOKEN_FILE"], port=0,
                timeout_seconds=float(os.environ.get("FUSION_ADDIN_FIXTURE_TIMEOUT", "2")))
for event in (signal.SIGINT, signal.SIGTERM):
    signal.signal(event, lambda _signum, _frame: stopping.set())
bridge.start()
print(json.dumps({"url": bridge.url, "handlerHash": bridge.handler_hash, "sessionId": bridge.session_id}), flush=True)
try:
    while not stopping.is_set():
        try:
            key = events.get(timeout=0.05)
        except queue.Empty:
            continue
        prior_calls = len(calls)
        bridge.dispatch_on_main_thread(key)
        if len(calls) > prior_calls:
            # The bridge has published its retained result before this signal.
            print(json.dumps({"completedRequestId": calls[-1], "calls": len(calls)}), flush=True)
finally:
    bridge.stop()
    print(json.dumps({"stopped": True, "calls": len(calls)}), flush=True)
