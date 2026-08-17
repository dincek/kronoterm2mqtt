"""Health state of the publish loop, served over HTTP for container health checks."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import sys
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


def hard_exit(exit_code: int) -> None:
    """Leave the process immediately, whatever the other threads are doing.

    The publish loop can be stuck in a blocking socket call, where neither a signal
    handler nor sys.exit() from this thread would get us out. Flush first, because
    os._exit() skips the interpreter's cleanup.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    logging.shutdown()
    os._exit(exit_code)


class HealthWatchdog:
    """Ends the process when the loop has been unhealthy for too long.

    Docker does not act on a failing HEALTHCHECK by itself, so the restart is left to
    the container restart policy: the process exits non-zero and `restart:
    unless-stopped` starts it again. That keeps the recovery inside the container
    instead of handing the Docker socket to a watchdog container.
    """

    def __init__(
        self,
        state: HealthState,
        restart_after_seconds: float,
        check_interval: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        exit_func: Callable[[int], None] = hard_exit,
    ):
        self.state = state
        self.restart_after_seconds = restart_after_seconds
        self.check_interval = check_interval
        self.clock = clock
        self.exit_func = exit_func
        self.unhealthy_since: Optional[float] = None
        self.thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return self.restart_after_seconds > 0

    def check(self) -> None:
        """Look at the state once. Ends the process when the outage lasted too long."""
        if not self.enabled:
            return

        health = self.state.as_dict()
        if health['healthy']:
            if self.unhealthy_since is not None:
                logger.info('Healthy again, watchdog timer reset')
            self.unhealthy_since = None
            return

        now = self.clock()
        if self.unhealthy_since is None:
            self.unhealthy_since = now
            return

        unhealthy_for = now - self.unhealthy_since
        if unhealthy_for < self.restart_after_seconds:
            return

        problems = '; '.join(health['problems'])
        logger.critical(f'Unhealthy for {unhealthy_for:.0f}s ({problems}) - exiting so the container restarts')
        print(f'Unhealthy for {unhealthy_for:.0f}s: {problems} - exiting for a restart', flush=True)
        self.exit_func(1)

    def start(self) -> None:
        if not self.enabled:
            logger.info('Health watchdog is disabled ([health] restart_after_seconds = 0)')
            return

        logger.info(f'Health watchdog: restart after {self.restart_after_seconds}s of trouble')
        self.thread = threading.Thread(target=self._run, name='health-watchdog', daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            time.sleep(self.check_interval)
            try:
                self.check()
            except Exception as e:  # noqa: BLE001 - a broken watchdog must not kill the loop
                logger.error(f'Health watchdog check failed: {e}')
