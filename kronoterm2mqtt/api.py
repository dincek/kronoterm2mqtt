import logging
import select

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from rich.pretty import pprint

from kronoterm2mqtt.constants import MODBUS_TCP_GREETING_TIMEOUT, MODBUS_TCP_MIN_TIMEOUT
from kronoterm2mqtt.user_settings import HeatPump


logger = logging.getLogger(__name__)


class GreetingTolerantModbusTcpClient(ModbusTcpClient):
    """Modbus/TCP client that discards what the gateway says before it is asked.

    Serial-to-TCP gateways often send a "registration packet" (usually their MAC
    address) as soon as the connection is established. pymodbus reads those bytes as
    the beginning of an MBAP header, rejects the frame ("Invalid Modbus protocol id")
    and - because a rejected frame consumes nothing - keeps them in its buffer, so
    every following response is misread until the connection is dropped.

    Draining the socket once per fresh connection keeps the framer in sync. Gateways
    that stay quiet are unaffected: there is simply nothing to read.
    """

    greeting_timeout = MODBUS_TCP_GREETING_TIMEOUT
    greeting_max_bytes = 4096  # Guard against a gateway that never stops talking

    def connect(self) -> bool:
        was_connected = self.socket is not None
        connected = super().connect()
        if connected and not was_connected:
            self.discard_greeting()
        return connected

    def discard_greeting(self) -> bytes:
        """Read and drop everything the gateway sent before the first request."""
        greeting = b''
        while (sock := self.socket) is not None:
            try:
                readable, _, _ = select.select([sock], [], [], self.greeting_timeout)
                if not readable:
                    break
                chunk = sock.recv(256)
            except OSError as err:
                logger.debug(f'Ignoring error while draining the Modbus connection: {err}')
                break
            if not chunk:
                break
            greeting += chunk
            if len(greeting) >= self.greeting_max_bytes:
                logger.warning(f'Modbus gateway keeps sending data unasked, stopped after {len(greeting)} bytes')
                break

        if greeting:
            logger.warning(
                f'Discarded {len(greeting)} unsolicited bytes sent by the Modbus gateway'
                f' before the first request: {greeting.hex(" ")}'
            )
        return greeting


def get_modbus_client(heat_pump: HeatPump, definitions: dict, verbosity: int) -> ModbusSerialClient | ModbusTcpClient:
    print(f'Connect to {heat_pump.port}...')

    if heat_pump.port[0] == '/':  # Serial client starting with /dev
        conn_settings = definitions['connection']

        conn_kwargs = dict(
            baudrate=conn_settings['baudrate'],
            bytesize=conn_settings['bytesize'],
            parity=conn_settings['parity'],
            stopbits=conn_settings['stopbits'],
            timeout=heat_pump.timeout,
        )
        if verbosity:
            print('Connection arguments:')
            pprint(conn_kwargs)

        client = ModbusSerialClient(heat_pump.port, **conn_kwargs)
    else:  # TCP client as Host IP address or host name with optional :port
        host_port = heat_pump.port.rsplit(':', 1)
        host = host_port[0]
        port = int(host_port[1]) if len(host_port) > 1 else 502
        client = GreetingTolerantModbusTcpClient(
            host=host, port=port, timeout=max(heat_pump.timeout, MODBUS_TCP_MIN_TIMEOUT)
        )

    if verbosity > 1:
        print('connected:', client.connect())
        print(client)

    return client
