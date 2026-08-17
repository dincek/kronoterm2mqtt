import dataclasses
import errno
import logging
import sys

from bx_py_utils.path import assert_is_file
from cli_base.systemd.data_classes import BaseSystemdServiceInfo, BaseSystemdServiceTemplateContext
from cli_base.toml_settings.api import TomlSettings
from cli_base.toml_settings.deserialize import toml2dataclass
from ha_services.mqtt4homeassistant.data_classes import MqttSettings
from rich import print  # noqa
from rich.pretty import pprint
import tomlkit

from kronoterm2mqtt.constants import BASE_PATH


try:
    import tomllib  # New in Python 3.11
except ImportError:
    import tomli as tomllib  # noqa:F401


logger = logging.getLogger(__name__)

# Errors that mean "this file is not ours to write", not "something is broken":
READ_ONLY_ERRNOS = (errno.EROFS, errno.EACCES, errno.EPERM)


@dataclasses.dataclass
class MqttTlsSettings:
    """
    TLS/SSL settings for MQTT connection.

    Set 'enabled' to true to use TLS.
    Provide 'ca_certs' path for server certificate verification.
    Provide 'certfile' and 'keyfile' for mutual TLS (client certificate auth).
    """

    enabled: bool = False
    ca_certs: str = ''  # Path to CA certificate file
    certfile: str = ''  # Path to PEM-encoded client certificate
    keyfile: str = ''  # Path to PEM-encoded client private key
    insecure: bool = False  # If true, skip hostname verification


@dataclasses.dataclass
class HealthCheck:
    """
    HTTP endpoint that reports whether MQTT, Modbus and publishing are working.

    Used by the container HEALTHCHECK and by the "health" command. It listens on
    localhost only by default, so nothing is exposed outside the container.
    """

    enabled: bool = True
    host: str = '127.0.0.1'
    port: int = 8099
    stale_after_seconds: int = 60  # Data older than this counts as unhealthy


@dataclasses.dataclass
class HeatPump:
    """
    The "definitions_name" is the prefix of "kronoterm2mqtt/definitions/*.toml" files!
    """

    definitions_name: str = 'kronoterm_ksm'
    device_name: str = 'Heat Pump'  # Appearing in MQTT as Device
    model: str = 'ETERA'  # Just for MQTT device Model info
    port: str = '/dev/ttyUSB0'
    timeout: float = 0.5
    pooling_interval: int = 10  # Sensor update in seconds

    def get_definitions(self, verbosity) -> dict:
        definition_file_path = BASE_PATH / 'definitions' / f'{self.definitions_name}.toml'
        assert_is_file(definition_file_path)
        content = definition_file_path.read_text(encoding='UTF-8')
        definitions = tomllib.loads(content)

        if verbosity > 1:
            pprint(definitions)

        return definitions


@dataclasses.dataclass
class CustomEteraExpander:
    """
    Custom IO Expander with DS18S20 1-wire thermometers
    for controlling additional loops and solar pumps
    See CustomEteraExpander class for more info.
    """

    module_enabled: bool = False
    uid: str = 'etera_expander_module'
    name: str = 'Custom ETERA Expander Module'
    model: str = 'DIY'  # Just for MQTT sub-device model info
    mqtt_payload_prefix: str = 'expander'

    port: str = '/dev/ttyUSB1'
    port_speed: int = 115200

    timeout: float = 0.5
    number_of_thermometers: int = 9  # To check initially if settings are OK

    loop_operation: list = dataclasses.field(default_factory=lambda: [1, 1, 1, 0])
    loop_sensors: list = dataclasses.field(default_factory=lambda: [1, 0, 6, 5])
    loop_temperature: list = dataclasses.field(default_factory=lambda: [25.0, 25.0, 25.0, 25.0])  # At 0°C
    heating_curve_coefficient: float = 0.25  # : loop/outside temp °C

    solar_pump_operation: int = 1  # : 0 = disabled, 1 = enabled
    solar_pump_difference_on: float = 8.0  # : °C On(solar collector - solar tank bottom)
    solar_pump_difference_off: float = 3.0  # : °C Off(solar collector - solart tank bottom)
    intra_tank_circulation_operation: bool = True  # enabled. Additional source signal required!
    intra_tank_circulation_difference_on: float = 8.0  # : °C On > (solar tank top - Hydro B DHW)
    intra_tank_circulation_difference_off: float = 5.0  # : °C Off < (solar tank top - Hydro B DHW)
    #: id of solar collector, solar tank (top, bottom), Etera Hydro B DHW, DHW Circulator return
    solar_sensors: list = dataclasses.field(default_factory=lambda: [4, 3, 2, 8, 7])
    # Relays with id [0 to 3] are for loop circulation pumps
    solar_pump_relay_id: int = 4
    inter_tank_pump_relay_id: int = 5
    sensor_names: list = dataclasses.field(
        default_factory=lambda: [
            'Spalnice',
            'Mansarda',
            'Nadgaražje',
            'Pritličje',
            'Kolektorji',
            'Solarni zgoraj',
            'Solarni spodaj',
            'Bojler',
            'Cirkulacija',
        ]
    )
    # empty string means relay is not used (disabled)
    relay_names: list = dataclasses.field(
        default_factory=lambda: [
            'Črpalka spalnic',
            'Črpalka mansarde',
            'Črpalka nadgaražja',
            '',
            'Črpalka kolektorjev',
            'Cirkulacija med bojlerjema',
            '',
            '',
        ]
    )

    def get_definitions(self, verbosity) -> dict:
        definition_file_path = BASE_PATH / 'definitions' / f'{self.definitions_name}.toml'
        assert_is_file(definition_file_path)
        content = definition_file_path.read_text(encoding='UTF-8')
        definitions = tomllib.loads(content)

        if verbosity > 1:
            pprint(definitions)

        return definitions


@dataclasses.dataclass
class SystemdServiceTemplateContext(BaseSystemdServiceTemplateContext):
    """
    Context values for the systemd service file content.
    """

    verbose_service_name: str = 'kronoterm2mqtt'
    exec_start: str = f'{sys.argv[0]} publish-loop'


@dataclasses.dataclass
class SystemdServiceInfo(BaseSystemdServiceInfo):
    """
    Information for systemd helper functions.
    """

    template_context: SystemdServiceTemplateContext = dataclasses.field(default_factory=SystemdServiceTemplateContext)


@dataclasses.dataclass
class UserSettings:
    """KRONOTERM -> MQTT - settings

    See README for more information.

    At least you should specify MQTT settings to connect to the
    Mosquito server.
    """

    # Information about the MQTT server:
    mqtt: dataclasses = dataclasses.field(default_factory=MqttSettings)

    # TLS settings for MQTT connection:
    mqtt_tls: dataclasses = dataclasses.field(default_factory=MqttTlsSettings)

    systemd: dataclasses = dataclasses.field(default_factory=SystemdServiceInfo)

    # Health endpoint for the container health check:
    health: dataclasses = dataclasses.field(default_factory=HealthCheck)

    heat_pump: dataclasses = dataclasses.field(default_factory=HeatPump)

    custom_expander: dataclasses = dataclasses.field(default_factory=CustomEteraExpander)

    def __post_init__(self):
        """Modify the MQTT defaults"""
        self.mqtt.main_uid = 'kronoterm'
        self.mqtt.host = 'mqtt.your-server.tld'


class ReadOnlyTolerantTomlSettings(TomlSettings):
    """Settings that can also be loaded from a file the app is not allowed to write.

    When the settings file gains new keys (e.g. a new section after an upgrade),
    cli_base writes them back and keeps a backup first. The container mounts the
    settings read-only on purpose, so that write fails - but the values themselves are
    already complete, and refusing to start over a file the app is not supposed to
    modify would be worse than running with them.
    """

    def get_user_settings(self, *, debug: bool = False) -> dataclasses:
        try:
            return super().get_user_settings(debug=debug)
        except OSError as err:
            if err.errno not in READ_ONLY_ERRNOS:
                raise
            logger.warning(f'Cannot update the settings file ({err}) - continuing with the values as they are')
            print(f'[yellow]Settings file is read-only ({err.strerror}), not updating it')
            document = tomlkit.loads(self.file_path.read_text(encoding='UTF-8'))
            toml2dataclass(document=document, instance=self.settings_dataclass)
            return self.settings_dataclass


def get_toml_settings() -> TomlSettings:
    return ReadOnlyTolerantTomlSettings(
        dir_name='kronoterm2mqtt',
        file_name='kronoterm2mqtt',
        settings_dataclass=UserSettings(),
    )


def get_user_settings(verbosity: int) -> UserSettings:
    toml_settings: TomlSettings = get_toml_settings()
    user_settings: UserSettings = toml_settings.get_user_settings(debug=verbosity > 0)
    return user_settings
