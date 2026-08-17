from pathlib import Path


CLI_EPILOG = 'Project Homepage: https://github.com/kosl/kronoterm2mqtt'

BASE_PATH = Path(__file__).parent


DEFAULT_DEVICE_MANUFACTURER = 'KRONOTERM'

MODBUS_SLAVE_ID = 20  # Kronoterm System Module Modbus address

# Modbus resilience: the heat pump sometimes drops the TCP connection, so retry
# instead of letting the exception kill the publish loop.
MODBUS_READ_ATTEMPTS = 3  # Attempts per register block before skipping this cycle
MODBUS_RETRY_DELAY = 1.0  # Seconds to wait before retrying a failed Modbus read
MODBUS_WRITE_ATTEMPTS = 3  # Attempts per register write before giving up
# The configured `timeout` default suits a serial line; a TCP gateway needs more headroom.
MODBUS_TCP_MIN_TIMEOUT = 3.0

# Etera expander module constants

MIXING_VALVE_HOLD_TIME = 120  # time between motor movements in seconds
