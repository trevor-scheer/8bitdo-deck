import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from eightbitdo.catalog import Artifact, Release
from eightbitdo.jobs import JobStore, default_state_dir
from eightbitdo.platform import DesktopPlatform, Platform, WorkerLaunch, current_platform, process_start
from eightbitdo.service import Service


class DesktopPlatformTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch('eightbitdo.platform.sys.platform', 'darwin'):
            self.platform = DesktopPlatform()
        self.service = Service(self.root, platform=self.platform, catalog=Mock())

    def test_platform_selection_does_not_open_hardware(self):
        for name in ('darwin', 'win32'):
            with self.subTest(name=name), patch('eightbitdo.platform.sys.platform', name), \
                    patch('eightbitdo.hidapi_backend._hid') as hid:
                self.assertIsInstance(current_platform(), DesktopPlatform)
                hid.assert_not_called()

    def test_backend_imports_without_posix_locking_module(self):
        code = '''import builtins, sys
original = builtins.__import__
def without_fcntl(name, *args, **kwargs):
    if name == "fcntl":
        raise ImportError("Windows has no fcntl")
    return original(name, *args, **kwargs)
builtins.__import__ = without_fcntl
from eightbitdo.service import Service
from eightbitdo.api import UpdaterAPI
assert UpdaterAPI().call("info")["platform_supported"]
'''
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
        result = subprocess.run([sys.executable, '-c', code], env=environment,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_write_or_boot_entrypoint_fails_before_device_access(self):
        with patch('eightbitdo.hidapi_backend._hid') as hid:
            for call in (lambda: self.service.install_update('a' * 32),
                         lambda: self.service.recover_update('a' * 32, True),
                         lambda: self.service.check_recovery('a' * 32),
                         lambda: self.service.run_install({'backend': 'hid'}),
                         lambda: self.service.run_recover({}),
                         lambda: self.platform.wait_for_boot(None),
                         lambda: self.platform.worker_launch(self.root, 'a' * 32, 'install', inhibit=True)):
                with self.subTest(call=call), self.assertRaisesRegex(RuntimeError, 'not enabled'):
                    call()
            hid.assert_not_called()
        self.assertEqual(self.platform.firmware_service().devices(), [])
        self.assertFalse(self.service.get_recovery_access()['can_setup'])

    def test_download_validates_firmware_without_probing_source_or_authorizing_write(self):
        image = struct.pack('<7I', 106, 0x6000, 4, 0x6013, 0, 0, 0) + b'test'
        path = self.root / 'image.dat'
        path.write_bytes(image)
        release = Release(95, 'release1', '1.06', 'receiver.dat',
                          'https://download.8bitdo.com/receiver.dat', len(image), '0' * 32,
                          '2026-11-21', '', False)
        self.service.catalog.list_releases.return_value = [release]
        self.service.catalog.download.return_value = Artifact(str(path), hashlib.sha256(image).hexdigest(),
                                                              release, False, 'No vendor signature.')
        job = self.service.store.create('usb-' + 'a' * 16, 'ultimate2-receiver', 'release1')
        with patch.object(self.platform, 'open_hid') as opened, \
                patch.object(self.platform, 'discover') as discovery:
            self.service.run_prepare(job)
        opened.assert_not_called()
        discovery.assert_not_called()
        self.assertEqual(job['state'], 'blocked')
        self.assertFalse(job['can_install'])
        self.assertIn('not enabled', job['message'])

    def test_windows_workers_detach_without_a_console_or_system_python(self):
        launch = WorkerLaunch(['updater.exe', '--worker', str(self.root), 'a' * 32, 'prepare'], {}, self.root)
        with patch('eightbitdo.platform.sys.platform', 'win32'), \
                patch('eightbitdo.platform.subprocess.DETACHED_PROCESS', 8, create=True), \
                patch('eightbitdo.platform.subprocess.CREATE_NEW_PROCESS_GROUP', 512, create=True), \
                patch('eightbitdo.platform.subprocess.Popen') as start:
            self.platform.start_worker(launch, self.root / 'job.log')
        self.assertEqual(start.call_args.kwargs['creationflags'], 520)
        self.assertNotIn('start_new_session', start.call_args.kwargs)
        self.assertEqual(start.call_args.args[0], launch.command)
        self.assertTrue(start.call_args.kwargs['close_fds'])

    def test_native_state_path_uses_platformdirs_without_changing_linux_default(self):
        directories = Mock()
        directories.user_state_path.return_value = self.root / 'native'
        for host in ('darwin', 'win32'):
            with self.subTest(host=host), patch('eightbitdo.jobs.sys.platform', host), \
                    patch.dict(sys.modules, {'platformdirs': directories}):
                self.assertEqual(default_state_dir(), self.root / 'native')
        directories.user_state_path.assert_called_with('8BitDo Updater', appauthor=False)
        with patch('eightbitdo.jobs.sys.platform', 'linux'), patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root)}):
            self.assertEqual(default_state_dir(), self.root / 'eightbitdo-deck')

    def test_process_creation_time_detects_reused_or_missing_pid(self):
        processes = Mock()
        processes.Error = type('ProcessError', (Exception,), {})
        processes.Process.return_value.create_time.return_value = 1234.5
        with patch('eightbitdo.platform.sys.platform', 'darwin'), patch.dict(sys.modules, {'psutil': processes}):
            self.assertEqual(process_start(123), '1234.5')
            self.assertTrue(self.platform.worker_running(123, '1234.5'))
            self.assertFalse(self.platform.worker_running(123, '1111'))
            processes.Process.side_effect = processes.Error('gone')
            self.assertFalse(self.platform.worker_running(123, '1234.5'))


class ApplicationMutationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.platform = Mock(native_write=True)
        self.platform.worker_running.return_value = False
        self.service = Service(self.root, platform=self.platform, catalog=Mock(), fwupd=Mock())

    def test_application_guard_refuses_active_jobs_and_live_completed_workers(self):
        job = self.service.store.create('', 'ultimate2-receiver', 'release')
        with self.assertRaisesRegex(RuntimeError, 'firmware jobs'):
            with self.service.app_update_guard():
                pass
        job['state'] = 'complete'
        self.service.store.save(job)
        self.platform.worker_running.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'firmware jobs'):
            with self.service.app_update_guard():
                pass
        self.platform.worker_running.return_value = False
        with self.service.app_update_guard():
            with self.assertRaises(RuntimeError):
                self.service.prepare_update('', 'ultimate2-receiver', 'release')
        self.platform.start_worker.assert_not_called()

    def test_pending_marker_blocks_queue_but_allows_application_recovery(self):
        (self.root / 'app-update-pending.json').write_text('{}')
        for call in (lambda: self.service.prepare_update('', 'ultimate2-receiver', 'release'),
                     lambda: self.service.install_update('a' * 32),
                     lambda: self.service.recover_update('a' * 32, True)):
            with self.subTest(call=call), self.assertRaisesRegex(RuntimeError, 'pending'):
                call()
        with self.service.app_update_guard():
            pass
        self.platform.start_worker.assert_not_called()

    def test_queue_holds_mutation_lock_until_persistent_job_and_worker_launch(self):
        def launch(*args):
            queued = list(self.service.store.directory.glob('*.json'))
            self.assertEqual(len(queued), 1)
            self.assertEqual(json.loads(queued[0].read_text())['state'], 'queued')
            with self.assertRaises(RuntimeError):
                with self.service.app_update_guard():
                    pass
        self.platform.start_worker.side_effect = launch
        result = self.service.prepare_update('', 'ultimate2-receiver', 'release')
        self.assertIn('job_id', result)
        self.platform.start_worker.assert_called_once()

    def test_job_lock_excludes_another_process(self):
        # Exercise the native lock implementation on every CI operating system.
        code = ('from pathlib import Path; from eightbitdo.jobs import JobStore; '
                's = JobStore(Path(__import__("sys").argv[1])); '
                's.lock("application-mutation").__enter__()')
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
        with self.service.store.lock('application-mutation'):
            result = subprocess.run([sys.executable, '-c', code, str(self.root)], env=environment,
                                    capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Another operation', result.stderr)

    def test_windows_job_save_retries_transient_sharing_conflict_atomically(self):
        job = self.service.store.create('', 'ultimate2-receiver', 'release')
        original_replace = os.replace
        attempts = []
        def replace(source, destination):
            attempts.append(source)
            if len(attempts) == 1:
                raise PermissionError('Reader holds file')
            return original_replace(source, destination)
        with patch('eightbitdo.jobs.sys.platform', 'win32'), \
                patch('eightbitdo.jobs.os.replace', side_effect=replace), \
                patch('eightbitdo.jobs.time.sleep'):
            job['message'] = 'Replaced atomically'
            self.service.store.save(job)
        self.assertEqual(self.service.store.read(job['id'])['message'], 'Replaced atomically')
        self.assertEqual(len(attempts), 2)


if __name__ == '__main__':
    unittest.main()
