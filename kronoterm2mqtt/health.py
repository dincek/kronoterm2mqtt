"""Health state of the publish loop, served over HTTP for container health checks."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import threading
import time
from typing import Callable, Optional


logger = logging.getLogger(__name__)

HEALTH_PATHS = ('/', '/health')


class HealthState:
    """What the publish loop knows about itself, readable from the HTTP thread.

    Ages are measured with a monotonic clock, so a system clock change cannot make
    stale data look fresh.
    """

    def __init__(self, stale_after_seconds: float, mqtt_host: str = '', modbus_port: str = ''):
        self.stale_after_seconds = stale_after_seconds
        self.mqtt_host = mqtt_host
        self.modbus_port = modbus_port

        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._mqtt_connected: Optional[Callable[[], bool]] = None
        self._last_read: Optional[float] = None
        self._last_read_complete: Optional[bool] = None
        self._last_publish: Optional[float] = None
        self._published_count = 0
        self._failed_reads = 0
        self._last_error: Optional[str] = None

    def set_mqtt_client(self, mqtt_client) -> None:
        """Ask the paho client itself, so a silent disconnect is noticed."""
        self._mqtt_connected = mqtt_client.is_connected

    def record_modbus_read(self, *, complete: bool) -> None:
        with self._lock:
            self._last_read = time.monotonic()
            self._last_read_complete = complete

    def record_modbus_failure(self, error: str) -> None:
        with self._lock:
            self._failed_reads += 1
            self._last_error = error

    def record_publish(self, published_count: int) -> None:
        with self._lock:
            self._last_publish = time.monotonic()
            self._published_count = published_count

    def mqtt_connected(self) -> bool:
        if self._mqtt_connected is None:
            return False
        try:
            return bool(self._mqtt_connected())
        except Exception as e:  # noqa: BLE001 - the health endpoint must never raise
            logger.debug(f'Could not ask the MQTT client for its state: {e}')
            return False

    def as_dict(self) -> dict:
        now = time.monotonic()
        with self._lock:
            read_age = None if self._last_read is None else round(now - self._last_read, 1)
            publish_age = None if self._last_publish is None else round(now - self._last_publish, 1)
            state = dict(
                uptime_seconds=round(now - self._started, 1),
                mqtt=dict(
                    connected=self.mqtt_connected(),
                    host=self.mqtt_host,
                    last_publish_seconds_ago=publish_age,
                ),
                modbus=dict(
                    port=self.modbus_port,
                    last_read_seconds_ago=read_age,
                    last_read_complete=self._last_read_complete,
                    failed_reads=self._failed_reads,
                    last_error=self._last_error,
                ),
                sensors_published=self._published_count,
                stale_after_seconds=self.stale_after_seconds,
            )

        state['problems'] = self._problems(state)
        state['healthy'] = not state['problems']
        return state

    def _problems(self, state: dict) -> list[str]:
        problems = []
        if not state['mqtt']['connected']:
            problems.append('MQTT client is not connected')

        read_age = state['modbus']['last_read_seconds_ago']
        if read_age is None:
            problems.append('no Modbus read completed yet')
        elif read_age > self.stale_after_seconds:
            problems.append(f'last Modbus read was {read_age}s ago')

        publish_age = state['mqtt']['last_publish_seconds_ago']
        if publish_age is None:
            problems.append('nothing published yet')
        elif publish_age > self.stale_after_seconds:
            problems.append(f'last publish was {publish_age}s ago')

        return problems

    def is_healthy(self) -> bool:
        return self.as_dict()['healthy']


class HealthRequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    state: HealthState  # Set by HealthServer

    def do_GET(self):  # noqa: N802 - name defined by BaseHTTPRequestHandler
        if self.path.split('?')[0] not in HEALTH_PATHS:
            self.send_error(404, 'Only /health is served here')
            return

        state = self.state.as_dict()
        body = json.dumps(state, indent=2).encode('utf-8')

        self.send_response(200 if state['healthy'] else 503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - signature defined by the base class
        logger.debug('health request: ' + format, *args)


class HealthServer:
    """Serves HealthState on a background thread. Never blocks the publish loop."""

    def __init__(self, state: HealthState, host: str, port: int):
        self.state = state
        self.host = host
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> int:
        """Start serving and return the port actually bound (useful with port=0 in tests)."""
        handler = type('BoundHealthRequestHandler', (HealthRequestHandler,), {'state': self.state})
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='health-server', daemon=True)
        self.thread.start()
        logger.info(f'Health endpoint listening on http://{self.host}:{self.port}/health')
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
