import errno
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from cli_base.toml_settings.api import TomlSettings

from kronoterm2mqtt.user_settings import UserSettings, get_toml_settings, get_user_settings


# A settings file from before the [health] section existed:
OLD_SETTINGS = """
[mqtt]
host = "mqtt.example.com"
port = 8883

[heat_pump]
port = "192.168.1.2:502"
"""


class ReadOnlySettingsTestCase(TestCase):
    def settings_file(self, temp_dir: str) -> Path:
        path = Path(temp_dir) / 'kronoterm2mqtt'
        path.mkdir()
        settings_file = path / 'kronoterm2mqtt.toml'
        settings_file.write_text(OLD_SETTINGS, encoding='UTF-8')
        return settings_file

    def test_missing_sections_are_added_to_a_writable_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_file = self.settings_file(temp_dir)
            with patch.object(TomlSettings, 'settings_directories', (temp_dir,)):
                user_settings: UserSettings = get_user_settings(verbosity=0)

            self.assertIn('[health]', settings_file.read_text(encoding='UTF-8'))

        self.assertEqual(user_settings.mqtt.host, 'mqtt.example.com')
        self.assertEqual(user_settings.health.port, 8099)

    def test_read_only_settings_file_does_not_stop_the_app(self):
        """The container mounts the settings read-only, so the update must be skipped."""
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_file = self.settings_file(temp_dir)
            read_only = OSError(errno.EROFS, 'Read-only file system')

            with (
                patch.object(TomlSettings, 'settings_directories', (temp_dir,)),
                patch('cli_base.toml_settings.api.backup', side_effect=read_only),
            ):
                user_settings: UserSettings = get_user_settings(verbosity=0)

            self.assertEqual(settings_file.read_text(encoding='UTF-8'), OLD_SETTINGS)  # Untouched

        # Values from the file, defaults for everything the file does not know about:
        self.assertEqual(user_settings.mqtt.host, 'mqtt.example.com')
        self.assertEqual(user_settings.heat_pump.port, '192.168.1.2:502')
        self.assertEqual(user_settings.health.port, 8099)
        self.assertTrue(user_settings.health.enabled)

    def test_other_os_errors_are_not_swallowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.settings_file(temp_dir)
            no_space = OSError(errno.ENOSPC, 'No space left on device')

            with (
                patch.object(TomlSettings, 'settings_directories', (temp_dir,)),
                patch('cli_base.toml_settings.api.backup', side_effect=no_space),
                self.assertRaises(OSError) as context,
            ):
                get_user_settings(verbosity=0)

        self.assertEqual(context.exception.errno, errno.ENOSPC)

    def test_print_settings_also_survives_a_read_only_file(self):
        """print-settings goes through TomlSettings directly, not through get_user_settings()."""
        with tempfile.TemporaryDirectory() as temp_dir:
            self.settings_file(temp_dir)
            read_only = OSError(errno.EROFS, 'Read-only file system')

            with (
                patch.object(TomlSettings, 'settings_directories', (temp_dir,)),
                patch('cli_base.toml_settings.api.backup', side_effect=read_only),
            ):
                get_toml_settings().print_settings()
