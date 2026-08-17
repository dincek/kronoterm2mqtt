from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from pymodbus.exceptions import ConnectionException
from pymodbus.pdu import ExceptionResponse
from pymodbus.pdu.register_message import ReadHoldingRegistersResponse

from kronoterm2mqtt.mqtt_handler import KronotermMqttHandler


def make_handler(modbus_client) -> KronotermMqttHandler:
    """A handler with just the attributes the Modbus read/write paths touch.

    Avoids __init__, which would open real MQTT and Modbus connections.
    """
    handler = object.__new__(KronotermMqttHandler)
    handler.verbosity = 0
    handler.health = None
    handler.modbus_client = modbus_client
    handler.registers = {}
    handler.address_ranges = [(2100, 2102)]
    return handler


@patch('kronoterm2mqtt.mqtt_handler.MODBUS_RETRY_DELAY', 0)
class ModbusRetryTestCase(IsolatedAsyncioTestCase):
    async def test_read_recovers_after_connection_error(self):
        modbus_client = MagicMock()
        modbus_client.read_holding_registers.side_effect = [
            ConnectionException('Connection unexpectedly closed'),
            ReadHoldingRegistersResponse(registers=[10, 20, 30]),
        ]

        handler = make_handler(modbus_client)
        complete = await handler.read_heat_pump_register_blocks()

        self.assertTrue(complete)
        self.assertEqual(handler.registers, {2100: 10, 2101: 20, 2102: 30})
        self.assertEqual(modbus_client.read_holding_registers.call_count, 2)
        modbus_client.connect.assert_called_once()  # Reconnected between the attempts

    async def test_read_gives_up_without_raising(self):
        modbus_client = MagicMock()
        modbus_client.read_holding_registers.side_effect = ConnectionException('Connection unexpectedly closed')

        handler = make_handler(modbus_client)
        complete = await handler.read_heat_pump_register_blocks()

        self.assertFalse(complete)
        self.assertEqual(handler.registers, {})
        self.assertEqual(modbus_client.read_holding_registers.call_count, 3)

    async def test_read_does_not_retry_device_error_response(self):
        modbus_client = MagicMock()
        modbus_client.read_holding_registers.return_value = ExceptionResponse(3, 2)  # Illegal data address

        handler = make_handler(modbus_client)
        complete = await handler.read_heat_pump_register_blocks()

        self.assertFalse(complete)
        self.assertEqual(modbus_client.read_holding_registers.call_count, 1)

    async def test_partial_read_keeps_the_registers_that_were_read(self):
        modbus_client = MagicMock()
        modbus_client.read_holding_registers.side_effect = [
            ReadHoldingRegistersResponse(registers=[1, 2, 3]),
            ConnectionException('Connection unexpectedly closed'),
            ConnectionException('Connection unexpectedly closed'),
            ConnectionException('Connection unexpectedly closed'),
        ]

        handler = make_handler(modbus_client)
        handler.address_ranges = [(2100, 2102), (2200, 2201)]
        complete = await handler.read_heat_pump_register_blocks()

        self.assertFalse(complete)
        self.assertEqual(handler.registers, {2100: 1, 2101: 2, 2102: 3})

    def test_write_recovers_after_connection_error(self):
        modbus_client = MagicMock()
        modbus_client.write_register.side_effect = [
            ConnectionException('Connection unexpectedly closed'),
            MagicMock(),
        ]

        handler = make_handler(modbus_client)

        self.assertTrue(handler.write_register(address=2000, value=1))
        self.assertEqual(modbus_client.write_register.call_count, 2)

    def test_write_gives_up_without_raising(self):
        modbus_client = MagicMock()
        modbus_client.write_register.side_effect = ConnectionException('Connection unexpectedly closed')

        handler = make_handler(modbus_client)

        self.assertFalse(handler.write_register(address=2000, value=1))
        self.assertEqual(modbus_client.write_register.call_count, 3)

    def test_reconnect_survives_a_failing_client(self):
        modbus_client = MagicMock()
        modbus_client.connect.side_effect = ConnectionException('No route to host')

        handler = make_handler(modbus_client)

        self.assertFalse(handler.reconnect_modbus())
