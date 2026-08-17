import json
import time
from unittest import TestCase
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

from kronoterm2mqtt.health import HealthServer, HealthState
from kronoterm2mqtt.tests.loopback import loopback_connection


def make_state(*, connected=True, stale_after_seconds=60) -> HealthState:
    state = HealthState(
        stale_after_seconds=stale_after_seconds,
        mqtt_host='mqtt.example.com',
        modbus_port='192.168.1.2:502',
    )
    mqtt_client = MagicMock()
    mqtt_client.is_connected.return_value = connected
    state.set_mqtt_client(mqtt_client)
    return state


class HealthStateTestCase(TestCase):
    def test_fresh_state_is_healthy(self):
        state = make_state()
        state.record_modbus_read(complete=True)
        state.record_publish(published_count=96)

        health = state.as_dict()
        self.assertEqual(health['problems'], [])
        self.assertTrue(health['healthy'])
        self.assertEqual(health['sensors_published'], 96)
        self.assertEqual(health['modbus']['port'], '192.168.1.2:502')

    def test_nothing_read_yet_is_unhealthy(self):
        state = make_state()

        health = state.as_dict()
        self.assertFalse(health['healthy'])
        self.assertEqual(health['problems'], ['no Modbus read completed yet', 'nothing published yet'])

    def test_disconnected_mqtt_is_unhealthy(self):
        state = make_state(connected=False)
        state.record_modbus_read(complete=True)
        state.record_publish(published_count=96)

        health = state.as_dict()
        self.assertFalse(health['healthy'])
        self.assertEqual(health['problems'], ['MQTT client is not connected'])

    def test_stale_data_is_unhealthy(self):
        state = make_state(stale_after_seconds=0.05)
        state.record_modbus_read(complete=True)
        state.record_publish(published_count=96)
        time.sleep(0.1)

        health = state.as_dict()
        self.assertFalse(health['healthy'])
        self.assertEqual(len(health['problems']), 2)
        self.assertIn('last Modbus read was', health['problems'][0])
        self.assertIn('last publish was', health['problems'][1])

    def test_failed_reads_are_counted_without_making_data_fresh(self):
        state = make_state()
        state.record_modbus_failure('Connection unexpectedly closed')

        health = state.as_dict()
        self.assertEqual(health['modbus']['failed_reads'], 1)
        self.assertEqual(health['modbus']['last_error'], 'Connection unexpectedly closed')
        self.assertIsNone(health['modbus']['last_read_seconds_ago'])
        self.assertFalse(health['healthy'])


@patch('socket.create_connection', loopback_connection)
class HealthServerTestCase(TestCase):
    def get(self, port: int, path: str = '/health'):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as err:
            body = err.read()
            return err.code, json.loads(body) if body.startswith(b'{') else body

    def test_healthy_state_is_served_as_200(self):
        state = make_state()
        state.record_modbus_read(complete=True)
        state.record_publish(published_count=96)

        with HealthServer(state=state, host='127.0.0.1', port=0) as server:
            status, payload = self.get(server.port)

        self.assertEqual(status, 200)
        self.assertTrue(payload['healthy'])
        self.assertTrue(payload['mqtt']['connected'])

    def test_unhealthy_state_is_served_as_503(self):
        state = make_state(connected=False)

        with HealthServer(state=state, host='127.0.0.1', port=0) as server:
            status, payload = self.get(server.port)

        self.assertEqual(status, 503)
        self.assertFalse(payload['healthy'])
        self.assertIn('MQTT client is not connected', payload['problems'])

    def test_unknown_path_is_404(self):
        state = make_state()

        with HealthServer(state=state, host='127.0.0.1', port=0) as server:
            status, _payload = self.get(server.port, path='/secrets')

        self.assertEqual(status, 404)
