"""Synthetic mission activity with independent health and Prometheus listeners.

This is a disposable demonstration service, not an operational mission system.
The application has no built-in telemetry fault: the qualified lab's network or
mesh policy must cause and repair the loss of metrics reachability.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest


def _port(value: str | int) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return port


def _interval(value: str | float) -> float:
    interval = float(value)
    if not math.isfinite(interval) or not 0 < interval <= 3600:
        raise ValueError("event interval must be finite and between 0 and 3600 seconds")
    return interval


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def handle_error(self, request, client_address):
        # No request content, addresses, or arbitrary exception bodies in logs.
        pass


class MissionService:
    """Own two listeners and one synthetic producer; close all of them together."""

    def __init__(self, host="127.0.0.1", health_port=8080, metrics_port=9090, event_interval=1.0):
        self.host = str(ipaddress.IPv4Address(host))
        self.health_port = _port(health_port)
        self.metrics_port = _port(metrics_port)
        if self.health_port != 0 and self.health_port == self.metrics_port:
            raise ValueError("health and metrics ports must be distinct")
        self.event_interval = _interval(event_interval)
        self.registry = CollectorRegistry()
        self.events = Counter("mission_events_total", "Synthetic mission events generated.", registry=self.registry)
        self.last_event = Gauge(
            "mission_last_event_timestamp_seconds", "Unix time of the last synthetic event.", registry=self.registry
        )
        self._stop = threading.Event()
        self._servers: list[_Server] = []
        self._threads: list[threading.Thread] = []
        self._started = False

    def _handler(self, endpoint):
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "mission-status"
            sys_version = ""

            def log_message(self, format, *args):
                # Paths and headers may contain arbitrary caller-provided data.
                pass

            def _respond(self, include_body=True):
                if self.path != endpoint:
                    self._send(404, b'{"error":"not_found"}\n', "application/json", include_body)
                elif endpoint == "/health":
                    body = b'{"status":"ok","service":"mission-status","data":"synthetic"}\n'
                    self._send(200, body, "application/json", include_body)
                else:
                    self._send(200, generate_latest(service.registry), CONTENT_TYPE_LATEST, include_body)

            def _send(self, status, body, content_type, include_body=True):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                if include_body:
                    self.wfile.write(body)

            def do_GET(self):
                self._respond()

            def do_HEAD(self):
                self._respond(include_body=False)

            def do_POST(self):
                self._send(405, b'{"error":"method_not_allowed"}\n', "application/json")

            do_PUT = do_POST
            do_PATCH = do_POST
            do_DELETE = do_POST

        return Handler

    def _produce(self):
        while not self._stop.is_set():
            self.events.inc()
            self.last_event.set(time.time())
            self._stop.wait(self.event_interval)

    @property
    def health_address(self):
        if not self._servers:
            raise RuntimeError("service is not running")
        return self._servers[0].server_address

    @property
    def metrics_address(self):
        if len(self._servers) < 2:
            raise RuntimeError("service is not running")
        return self._servers[1].server_address

    @property
    def running(self):
        return len(self._threads) == 3 and all(thread.is_alive() for thread in self._threads)

    def start(self):
        if self._started:
            raise RuntimeError("a service instance can only be started once")
        self._started = True
        try:
            # Bind both before starting threads. A failed second bind closes the first.
            self._servers.append(_Server((self.host, self.health_port), self._handler("/health")))
            self._servers.append(_Server((self.host, self.metrics_port), self._handler("/metrics")))
            for server in self._servers:
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                thread.start()
                self._threads.append(thread)
            producer = threading.Thread(target=self._produce, daemon=True)
            producer.start()
            self._threads.append(producer)
        except Exception:
            self.stop()
            raise
        return self

    def stop(self):
        self._stop.set()
        for index, server in enumerate(self._servers):
            if index < len(self._threads) and self._threads[index].is_alive():
                server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=3)
        self._servers.clear()
        self._threads.clear()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 bind address; container entry point uses 0.0.0.0")
    parser.add_argument("--health-port", type=_port, default=8080)
    parser.add_argument("--metrics-port", type=_port, default=9090)
    parser.add_argument("--event-interval", type=_interval, default=1.0)
    args = parser.parse_args(argv)
    try:
        service = MissionService(args.host, args.health_port, args.metrics_port, args.event_interval)
    except ValueError as exc:
        parser.error(str(exc))
    stopped = threading.Event()
    old_handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        old_handlers[signum] = signal.signal(signum, lambda _signum, _frame: stopped.set())
    try:
        with service:
            print(json.dumps({"service": "mission-status", "data": "synthetic",
                              "health_port": service.health_address[1],
                              "metrics_port": service.metrics_address[1]}), flush=True)
            while not stopped.wait(0.1):
                if not service.running:
                    return 1
    except OSError:
        parser.exit(1, "mission-status could not bind the requested listeners\n")
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
