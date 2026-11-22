import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from eightbitdo.platform import LinuxPlatform


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux user session integration')
class UserSessionTests(unittest.TestCase):
    def launch(self, environment, *, directory=None, bus=None, error=None):
        directory = directory or SimpleNamespace(st_uid=2345, st_mode=stat.S_IFDIR | 0o700)
        bus = bus or SimpleNamespace(st_uid=2345, st_mode=stat.S_IFSOCK | 0o666)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, environment, clear=True), \
                patch('eightbitdo.platform.os.geteuid', return_value=2345), \
                patch('eightbitdo.platform.Path.lstat', side_effect=error or [directory, bus]), \
                patch('eightbitdo.platform.shutil.which', side_effect=lambda name: '/usr/bin/' + name):
            return LinuxPlatform().worker_launch(Path(temporary), 'a' * 32, 'install', inhibit=True)

    def test_decky_without_session_variables_uses_current_users_bus(self):
        launch = self.launch({})
        self.assertEqual(launch.environment['XDG_RUNTIME_DIR'], '/run/user/2345')
        self.assertEqual(launch.environment['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=/run/user/2345/bus')
        self.assertIn('--user', launch.command)
        self.assertIn('/usr/bin/systemd-inhibit', launch.command)
        self.assertIn('--what=sleep', launch.command)
        self.assertIn('--mode=block', launch.command)

    def test_configured_runtime_uses_its_verified_bus_and_escapes_address(self):
        launch = self.launch({'XDG_RUNTIME_DIR': '/run/session with spaces',
                              'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/9999/bus'})
        self.assertEqual(launch.environment['XDG_RUNTIME_DIR'], '/run/session with spaces')
        self.assertEqual(launch.environment['DBUS_SESSION_BUS_ADDRESS'],
                         'unix:path=/run/session%20with%20spaces/bus')

    def test_missing_session_fails_before_a_worker_can_be_launched(self):
        with self.assertRaisesRegex(RuntimeError, 'desktop session'):
            self.launch({}, error=FileNotFoundError())

    def test_foreign_or_shared_runtime_and_invalid_bus_are_rejected(self):
        for directory, bus in (
            (SimpleNamespace(st_uid=9999, st_mode=stat.S_IFDIR | 0o700), None),
            (SimpleNamespace(st_uid=2345, st_mode=stat.S_IFDIR | 0o777), None),
            (SimpleNamespace(st_uid=2345, st_mode=stat.S_IFLNK | 0o700), None),
            (None, SimpleNamespace(st_uid=9999, st_mode=stat.S_IFSOCK | 0o666)),
            (None, SimpleNamespace(st_uid=2345, st_mode=stat.S_IFREG | 0o600)),
        ):
            with self.subTest(directory=directory, bus=bus), self.assertRaisesRegex(RuntimeError, 'desktop session'):
                self.launch({}, directory=directory, bus=bus)


if __name__ == '__main__':
    unittest.main()
