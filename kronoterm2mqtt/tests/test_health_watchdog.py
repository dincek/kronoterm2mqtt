from unittest import TestCase
from unittest.mock import MagicMock

from kronoterm2mqtt.health import HealthState, HealthWatchdog


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_state() -> tuple[HealthState, MagicMock]:
    """A real HealthState that starts out healthy, plus the MQTT client to flip."""
    state = HealthState(stale_after_seconds=60, mqtt_host='mqtt.example.com', modbus_port='1.2.3.4:502')
    mqtt_client = MagicMock()
    mqtt_client.is_connected.return_value = True
    state.set_mqtt_client(mqtt_client)
    state.record_modbus_read(complete=True)
    state.record_publish(published_count=96)
    return state, mqtt_client


class HealthWatchdogTestCase(TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.state, self.mqtt_client = make_state()
        self.exits = []
        self.watchdog = HealthWatchdog(
            state=self.state,
            restart_after_seconds=300,
            check_interval=15,
            clock=self.clock,
            exit_func=self.exits.append,
        )

    def go_unhealthy(self):
        self.mqtt_client.is_connected.return_value = False

    def go_healthy(self):
        self.mqtt_client.is_connected.return_value = True

    def test_healthy_process_is_left_alone(self):
        for _ in range(100):
            self.watchdog.check()
            self.clock.advance(15)

        self.assertEqual(self.exits, [])

    def test_short_outage_does_not_restart(self):
        self.go_unhealthy()
        self.watchdog.check()
        self.clock.advance(299)
        self.watchdog.check()

        self.assertEqual(self.exits, [])

    def test_long_outage_exits_non_zero(self):
        self.go_unhealthy()
        self.watchdog.check()
        self.clock.advance(300)
        self.watchdog.check()

        self.assertEqual(self.exits, [1])

    def test_recovery_resets_the_timer(self):
        self.go_unhealthy()
        self.watchdog.check()
        self.clock.advance(299)

        self.go_healthy()  # Broker came back
        self.watchdog.check()
        self.clock.advance(10)

        self.go_unhealthy()
        self.watchdog.check()
        self.clock.advance(299)
        self.watchdog.check()

        self.assertEqual(self.exits, [])

    def test_disabled_watchdog_never_exits(self):
        watchdog = HealthWatchdog(
            state=self.state,
            restart_after_seconds=0,  # Disabled
            clock=self.clock,
            exit_func=self.exits.append,
        )
        self.go_unhealthy()

        watchdog.check()
        self.clock.advance(10_000)
        watchdog.check()

        self.assertEqual(self.exits, [])
        self.assertFalse(watchdog.enabled)
