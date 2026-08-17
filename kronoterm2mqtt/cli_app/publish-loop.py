import asyncio
from asyncio.exceptions import CancelledError
import logging
import time

from cli_base.cli_tools.verbosity import setup_logging
from cli_base.tyro_commands import TyroVerbosityArgType
from ha_services.exceptions import InvalidStateValue
from rich import print  # noqa

from kronoterm2mqtt.cli_app import app
from kronoterm2mqtt.health import HealthServer, HealthState
from kronoterm2mqtt.mqtt_connection import get_connected_client
from kronoterm2mqtt.mqtt_handler import KronotermMqttHandler
from kronoterm2mqtt.user_settings import UserSettings, get_user_settings


logger = logging.getLogger(__name__)

RESTART_DELAY = 5  # Seconds to wait before the first restart attempt
RESTART_DELAY_MAX = 300  # Upper bound for the exponential restart backoff
RESTART_DELAY_RESET = 300  # A run lasting this long counts as healthy and resets the backoff


@app.command
def test_mqtt_connection(verbosity: TyroVerbosityArgType):
    """
    Test connection to MQTT Server
    """
    setup_logging(verbosity=verbosity)
    user_settings: UserSettings = get_user_settings(verbosity=verbosity)

    mqttc = get_connected_client(user_settings=user_settings, verbosity=verbosity)
    mqttc.loop_start()
    mqttc.loop_stop()
    mqttc.disconnect()
    print('\n[green]Test succeed[/green], bye ;)')


@app.command
def publish_loop(verbosity: TyroVerbosityArgType):
    """
    Publish KRONOTERM registers to Home Assistant MQTT
    """
    setup_logging(verbosity=verbosity)
    user_settings: UserSettings = get_user_settings(verbosity=verbosity)

    restart_delay = RESTART_DELAY

    # The health state outlives the handler on purpose: while the loop is restarting
    # after a crash, the endpoint keeps answering and reports stale data instead of
    # refusing the connection.
    health = HealthState(
        stale_after_seconds=user_settings.health.stale_after_seconds,
        mqtt_host=user_settings.mqtt.host,
        modbus_port=user_settings.heat_pump.port,
    )
    if user_settings.health.enabled:
        HealthServer(state=health, host=user_settings.health.host, port=user_settings.health.port).start()

    while True:
        started = time.monotonic()
        try:
            print('[green]Starting Kronoterm 2 MQTT[/green]')
            with KronotermMqttHandler(user_settings=user_settings, verbosity=verbosity, health=health) as mqtt_handler:
                asyncio.run(mqtt_handler.publish_loop())
        except KeyboardInterrupt:
            raise
        except (InvalidStateValue, CancelledError) as e:
            logger.error(f'Kronoterm2MQTT loop failed. USB problem? {e}')
        except Exception as e:
            print(f'Error: {e}', type(e))
            logger.exception(f'Unhandled Exception: {e} {type(e)}')

        if time.monotonic() - started >= RESTART_DELAY_RESET:
            # The last run was healthy for a while, so start over with a short delay.
            restart_delay = RESTART_DELAY

        print(f'[yellow]Restarting in {restart_delay} seconds ...[/yellow]', flush=True)
        logger.warning(f'Restarting Kronoterm2MQTT in {restart_delay} seconds ...')
        time.sleep(restart_delay)
        restart_delay = min(restart_delay * 2, RESTART_DELAY_MAX)

