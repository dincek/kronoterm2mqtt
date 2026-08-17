import socket
import threading
from unittest import TestCase
from unittest.mock import patch

from pymodbus.pdu.register_message import ReadHoldingRegistersResponse

from kronoterm2mqtt.api import get_modbus_client
from kronoterm2mqtt.tests.loopback import loopback_connection
from kronoterm2mqtt.user_settings import HeatPump


# What the gateway in front of the heat pump sends right after the TCP connection
# is established: its MAC address as a "registration packet".
GREETING = bytes.fromhex('287a0e915ea9')

DEVICE_ID = 20
REGISTERS = (1, 2, 3)


class FakeGateway:
    """Modbus/TCP server that greets every new connection with unsolicited bytes."""

    def __init__(self, greeting: bytes = GREETING):
        self.greeting = greeting
        self.server = socket.create_server(('127.0.0.1', 0))
        self.server.settimeout(5)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.running = True

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.running = False
        self.server.close()
        self.thread.join(timeout=5)

    def _serve(self):
        while self.running:
            try:
                conn, _addr = self.server.accept()
            except (OSError, TimeoutError):
                return
            with conn:
                conn.settimeout(5)
                if self.greeting:
                    conn.sendall(self.greeting)
                while self.running:
                    try:
                        request = conn.recv(512)
                    except (OSError, TimeoutError):
                        break
                    if not request:
                        break
                    conn.sendall(self._response(request))

    @staticmethod
    def _response(request: bytes) -> bytes:
        transaction_id = request[0:2]
        payload = bytes([DEVICE_ID, 3, 2 * len(REGISTERS)])
        for value in REGISTERS:
            payload += value.to_bytes(2, 'big')
        return transaction_id + b'\x00\x00' + len(payload).to_bytes(2, 'big') + payload


@patch('socket.create_connection', loopback_connection)
class ModbusGreetingTestCase(TestCase):
    def test_read_works_although_the_gateway_greets_the_connection(self):
        with FakeGateway() as gateway:
            client = get_modbus_client(
                heat_pump=HeatPump(port=f'127.0.0.1:{gateway.port}'),
                definitions={},
                verbosity=0,
            )
            try:
                # Without the workaround the greeting desynchronizes the MBAP framer:
                # "Invalid Modbus protocol id: 3729" and a timeout before the retry.
                with self.assertNoLogs('pymodbus.logging', level='ERROR'):
                    response = client.read_holding_registers(address=2100, count=3, device_id=DEVICE_ID)
            finally:
                client.close()

        self.assertIsInstance(response, ReadHoldingRegistersResponse)
        self.assertEqual(response.registers, list(REGISTERS))

    def test_read_works_without_a_greeting(self):
        """A well behaved gateway must not be slowed down or broken by the workaround."""
        with FakeGateway(greeting=b'') as gateway:
            client = get_modbus_client(
                heat_pump=HeatPump(port=f'127.0.0.1:{gateway.port}'),
                definitions={},
                verbosity=0,
            )
            try:
                response = client.read_holding_registers(address=2100, count=3, device_id=DEVICE_ID)
            finally:
                client.close()

        self.assertEqual(response.registers, list(REGISTERS))
