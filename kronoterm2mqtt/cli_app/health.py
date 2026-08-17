import json
import logging
import sys
import urllib.error
import urllib.request

from cli_base.cli_tools.verbosity import setup_logging
from cli_base.tyro_commands import TyroVerbosityArgType
from rich import print  # noqa
from rich.table import Table

from kronoterm2mqtt.cli_app import app
from kronoterm2mqtt.user_settings import UserSettings, get_user_settings


logger = logging.getLogger(__name__)


def fetch_health(host: str, port: int, timeout: float = 5.0) -> dict:
    """Ask the running publish loop how it is doing. Raises URLError if it is not running."""
    url = f'http://{host}:{port}/health'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        # An unhealthy loop answers with 503 and the same payload - that is a result, not an error.
        return json.loads(err.read())


@app.command
def health(verbosity: TyroVerbosityArgType):
    """
    Show the status of the running publish loop (MQTT, Modbus, publishing)
    """
    setup_logging(verbosity=verbosity)
    user_settings: UserSettings = get_user_settings(verbosity=verbosity)
    settings = user_settings.health

    if not settings.enabled:
        print('[yellow]The health endpoint is disabled in the settings ([health] enabled = false)')
        sys.exit(2)

    try:
        state = fetch_health(host=settings.host, port=settings.port)
    except (urllib.error.URLError, OSError) as err:
        print(f'[red]No answer from http://{settings.host}:{settings.port}/health: {err}')
        print('[yellow]Is the publish loop running?')
        sys.exit(1)

    published = state['sensors_published']

    table = Table(show_header=False, box=None)
    table.add_row('MQTT', settings_value(state['mqtt']['connected']), state['mqtt']['host'])
    table.add_row('Last publish', seconds_ago(state['mqtt']['last_publish_seconds_ago']), f'{published} entities')
    table.add_row('Modbus', settings_value(state['modbus']['last_read_complete']), state['modbus']['port'])
    table.add_row('Last read', seconds_ago(state['modbus']['last_read_seconds_ago']), '')
    table.add_row('Failed reads', str(state['modbus']['failed_reads']), state['modbus']['last_error'] or '')
    table.add_row('Uptime', f'{state["uptime_seconds"]:.0f}s', '')

    print()
    if state['healthy']:
        print('[green]HEALTHY[/green]')
    else:
        print('[red]UNHEALTHY[/red]')
        for problem in state['problems']:
            print(f'  [red]-[/red] {problem}')
    print(table)

    sys.exit(0 if state['healthy'] else 1)


def settings_value(value) -> str:
    if value is None:
        return '[yellow]unknown'
    return '[green]OK' if value else '[red]FAILED'


def seconds_ago(value) -> str:
    if value is None:
        return '[yellow]never'
    return f'{value}s ago'
