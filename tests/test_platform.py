import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from eightbitdo.platform import LinuxPlatform, process_start
from eightbitdo.runtime import external_environment, frozen_application
from eightbitdo.service import Service


class LinuxPlatformTests(unittest.TestCase):
    def setUp(self):
        self.platform = LinuxPlatform()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job_id = 'a' * 32

    def test_source_worker_uses_current_interpreter_and_package_context(self):
        with patch('eightbitdo.platform.frozen_application', return_value=False), \
                patch('eightbitdo.platform.sys.frozen', False, create=True), \
                patch('eightbitdo.platform.sys.executable', '/isolated/venv/bin/python'):
            launch = self.platform.worker_launch(self.root, self.job_id, 'prepare', inhibit=False)
        self.assertEqual(launch.command, ['/isolated/venv/bin/python', '-m', 'eightbitdo.worker',
                                         str(self.root), self.job_id, 'prepare'])
        self.assertEqual(launch.environment['PYTHONPATH'], str(Path(__file__).resolve().parent.parent))
        self.assertEqual(launch.directory, Path(launch.environment['PYTHONPATH']))

    def test_frozen_worker_reexecutes_independently_without_temporary_paths(self):
        with patch('eightbitdo.platform.frozen_application', return_value=True), \
                patch('eightbitdo.platform.sys.frozen', True, create=True), \
                patch('eightbitdo.platform.sys.executable', '/opt/8bitdo/eightbitdo'), \
                patch.dict(os.environ, {'LD_LIBRARY_PATH': '/tmp/_MEI123', 'LD_LIBRARY_PATH_ORIG': '/host/lib',
                                        'PYTHONPATH': '/tmp/_MEI123', '_PYI_APPLICATION_HOME_DIR': '/tmp/_MEI123'}):
            launch = self.platform.worker_launch(self.root, self.job_id, 'prepare', inhibit=False)
        self.assertEqual(launch.command, ['/opt/8bitdo/eightbitdo', '--worker', str(self.root), self.job_id, 'prepare'])
        self.assertEqual(launch.environment['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertEqual(launch.environment['LD_LIBRARY_PATH'], '/host/lib')
        self.assertNotIn('PYTHONPATH', launch.environment)
        self.assertNotIn('_PYI_APPLICATION_HOME_DIR', launch.environment)
        self.assertEqual(launch.directory, self.root)
        self.assertNotIn('_MEI', repr(launch))

    def test_frozen_protected_worker_passes_restart_flag_through_user_manager(self):
        with patch('eightbitdo.platform.frozen_application', return_value=True), \
                patch('eightbitdo.platform.sys.executable', '/opt/8bitdo/eightbitdo'), \
                patch('eightbitdo.platform.shutil.which', side_effect=lambda name: '/usr/bin/' + name):
            launch = self.platform.worker_launch(self.root, self.job_id, 'recover', inhibit=True)
        self.assertEqual(launch.command[0], '/usr/bin/systemd-run')
        self.assertIn('--setenv=PYINSTALLER_RESET_ENVIRONMENT=1', launch.command)
        self.assertIn('--setenv=PYTHONPATH=', launch.command)
        self.assertIn('--working-directory=' + str(self.root), launch.command)
        self.assertIn('/usr/bin/systemd-inhibit', launch.command)
        self.assertEqual(launch.command[-5:], ['/opt/8bitdo/eightbitdo', '--worker', str(self.root), self.job_id, 'recover'])

    def test_frozen_host_uses_external_python_without_reexecuting_host(self):
        with patch('eightbitdo.platform.frozen_application', return_value=False), \
                patch('eightbitdo.platform.sys.frozen', True, create=True), \
                patch('eightbitdo.platform.sys.executable', '/opt/decky/PluginLoader'), \
                patch('eightbitdo.platform.shutil.which', return_value='/usr/bin/python3'):
            launch = self.platform.worker_launch(self.root, self.job_id, 'prepare', inhibit=False)
        self.assertEqual(launch.command[:3], ['/usr/bin/python3', '-m', 'eightbitdo.worker'])
        with patch('eightbitdo.platform.frozen_application', return_value=False), \
                patch('eightbitdo.platform.sys.frozen', True, create=True), \
                patch('eightbitdo.platform.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'python3'):
                self.platform.worker_launch(self.root, self.job_id, 'prepare', inhibit=False)

    def test_protected_workers_preserve_literal_paths_without_new_systemd_options(self):
        state = self.root / 'state $HOME ${USER} $$ with spaces'
        executable = '/opt/controller tools $HOME ${USER} $$/eightbitdo'
        source_root = '/opt/source tree $HOME ${USER} $$'
        for frozen in (False, True):
            with self.subTest(frozen=frozen), \
                    patch('eightbitdo.platform.frozen_application', return_value=frozen), \
                    patch('eightbitdo.platform.sys.frozen', False, create=True), \
                    patch('eightbitdo.platform.sys.executable', executable), \
                    patch('eightbitdo.platform.__file__', source_root + '/eightbitdo/platform.py'), \
                    patch('eightbitdo.platform.shutil.which', side_effect=lambda name: '/usr/bin/' + name):
                launch = self.platform.worker_launch(state, self.job_id, 'install', inhibit=True)
            self.assertFalse(any(arg.startswith('--expand-environment') for arg in launch.command))
            expected_worker = [executable, '--worker'] if frozen else [executable, '-m', 'eightbitdo.worker']
            expected_worker += [str(state), self.job_id, 'install']
            self.assertEqual(launch.command[-len(expected_worker):],
                             [arg.replace('$', '$$') for arg in expected_worker])
            self.assertIn('--working-directory=' + (str(state) if frozen else source_root), launch.command)
            self.assertIn('--setenv=PYTHONPATH=' + ('' if frozen else source_root), launch.command)
            self.assertIn('/usr/bin/systemd-inhibit', launch.command)
            self.assertIn('--what=sleep', launch.command)
            self.assertIn('--mode=block', launch.command)
            if frozen:
                self.assertIn('--setenv=PYINSTALLER_RESET_ENVIRONMENT=1', launch.command)

    def test_protected_worker_has_no_uninhibited_fallback(self):
        for missing in ('systemd-run', 'systemd-inhibit'):
            with self.subTest(missing=missing), \
                    patch('eightbitdo.platform.shutil.which', side_effect=lambda name: None if name == missing else '/usr/bin/' + name):
                with self.assertRaisesRegex(RuntimeError, 'prevent suspend'):
                    self.platform.worker_launch(self.root, self.job_id, 'install', inhibit=True)

    def test_invalid_worker_request_fails_before_launch_planning(self):
        for job_id, action in (('../../other', 'prepare'), (self.job_id, 'shell'), ('a' * 33, 'install')):
            with self.subTest(job_id=job_id, action=action), self.assertRaises(ValueError):
                self.platform.worker_launch(self.root, job_id, action, inhibit=False)

    def test_process_identity_handles_spaces_and_parentheses_in_comm(self):
        # Fields 3..21 followed by starttime (field 22).
        text = '123 (a process) name) ' + ' '.join(['S', *(['0'] * 18), '4567', '0'])
        with patch('eightbitdo.platform.Path.read_text', return_value=text):
            self.assertEqual(process_start(123), '4567')
            self.assertTrue(self.platform.worker_running(123, '4567'))
            self.assertFalse(self.platform.worker_running(123, '7654'))
            self.assertFalse(self.platform.worker_running(True, '4567'))
        with patch('eightbitdo.platform.Path.read_text', side_effect=FileNotFoundError):
            self.assertFalse(self.platform.worker_running(123, '4567'))

    def test_service_uses_injected_device_and_desktop_services(self):
        platform = Mock()
        platform.discover.return_value = []
        fwupd = Mock()
        fwupd.devices.return_value = []
        service = Service(self.root, platform=platform, fwupd=fwupd)
        self.assertEqual(service.scan_devices(), {'devices': [], 'warnings': []})
        platform.discover.assert_called_once_with()
        service.open_official_updater()
        platform.open_official_updater.assert_called_once_with('https://web.8bitdo.com/')
        self.assertIs(service.recovery_access, platform.recovery_access.return_value)

    def test_browser_uses_desktop_launcher_when_chrome_flatpak_is_absent(self):
        with patch('eightbitdo.platform.os.geteuid', return_value=1000), \
                patch('eightbitdo.platform.shutil.which', side_effect=lambda name: '/usr/bin/xdg-open' if name == 'xdg-open' else None), \
                patch('eightbitdo.platform.subprocess.Popen') as start, \
                patch('eightbitdo.platform.time.sleep'):
            start.return_value.poll.return_value = 0
            self.assertEqual(self.platform.open_official_updater('https://web.8bitdo.com/'), {'opened': True})
        self.assertEqual(start.call_args.args[0], ['/usr/bin/xdg-open', 'https://web.8bitdo.com/'])


class FrozenEnvironmentTests(unittest.TestCase):
    def test_our_package_must_be_inside_the_bundle_to_reexecute_it(self):
        package_root = Path(__file__).resolve().parent.parent
        with patch('eightbitdo.runtime.sys.frozen', True, create=True), \
                patch('eightbitdo.runtime.sys._MEIPASS', str(package_root), create=True):
            self.assertTrue(frozen_application())
        with patch('eightbitdo.runtime.sys.frozen', True, create=True), \
                patch('eightbitdo.runtime.sys._MEIPASS', '/different/host/bundle', create=True):
            self.assertFalse(frozen_application())

    def test_external_process_does_not_inherit_bundle_libraries(self):
        with patch('eightbitdo.runtime.sys.frozen', True, create=True), \
                patch.dict(os.environ, {'LD_LIBRARY_PATH': '/tmp/_MEI123', '_PYI_ARCHIVE_FILE': '/bundle'}, clear=True):
            self.assertNotIn('LD_LIBRARY_PATH', external_environment())
            self.assertNotIn('_PYI_ARCHIVE_FILE', external_environment())
        with patch('eightbitdo.runtime.sys.frozen', False, create=True), \
                patch.dict(os.environ, {'LD_LIBRARY_PATH': '/custom/lib'}, clear=True):
            self.assertEqual(external_environment()['LD_LIBRARY_PATH'], '/custom/lib')


if __name__ == '__main__':
    unittest.main()
