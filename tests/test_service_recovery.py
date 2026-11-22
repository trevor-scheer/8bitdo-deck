import sys
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from eightbitdo.connection import BootConnection
from eightbitdo.discovery import Device
from eightbitdo.protocol import DeviceIdentity
from eightbitdo.service import Service
from tests.test_recovery import BootDevice


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux host integration")
class RecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = Service(self.root, coordination_dir=self.root, catalog=Mock(), fwupd=Mock())
        self.service.recovery_access.status = Mock(return_value={'configured': True})
        self.boot = BootDevice()
        self.registry = patch('eightbitdo.qualifications.SOURCE_REVIEWED_AUTHORIZATIONS', (self.boot.authorization,))
        self.registry.start()
        self.addCleanup(self.registry.stop)
        image = self.boot.image
        path = self.root / 'firmware' / 'image.dat'
        path.parent.mkdir()
        path.write_bytes(struct.pack('<7I', image.version_raw, image.address, len(image.payload), image.pid,
                                     image.reserved1, image.revision, image.reserved2) + image.payload)
        port = '/sys/devices/fake/1-1'
        device_id = 'usb-' + hashlib.sha256(port.encode()).hexdigest()[:16]
        self.job = self.service.store.create(device_id, 'ultimate2-receiver', 'release1')
        self.job.update(state='failed', backend='hid', worker_action='install', original_usb_path=port, original_version_raw=101,
                        original_version='1.01', original_hardware_revision=None, version='1.06',
                        artifact_path=str(path), sha256=image.sha256,
                        evidence_level=self.boot.authorization.evidence_level, evidence=self.boot.authorization.evidence)
        self.service.store.save(self.job)
        self.device = Device(device_id, 'Receiver', '2dc8', '3208', usb_path=port)
        self.runtime = Device(device_id, 'Receiver', '2dc8', '6013', usb_path=port, hid_paths=[Path('/dev/fake')])
        discovery = patch('eightbitdo.platform.discover', return_value=[self.device])
        self.discovery = discovery.start()
        self.addCleanup(discovery.stop)
        context = MagicMock()
        context.__enter__.return_value = BootConnection(self.boot, 0x3208)
        open_boot = patch('eightbitdo.platform.wait_for_boot', return_value=context)
        self.open_boot = open_boot.start()
        self.addCleanup(open_boot.stop)

    def queue(self):
        self.service.check_recovery(self.job['id'])
        def spawn(job, action):
            job['worker_action'] = action
            self.service.store.save(job)
        with patch.object(self.service, '_spawn', side_effect=spawn) as start:
            result = self.service.recover_update(self.job['id'], confirmed=True)
        self.assertEqual(start.call_args.args[1], 'recover')
        self.assertEqual(result['state'], 'queued')
        return self.service.store.read(self.job['id'])

    def test_public_capability_requires_captured_source_and_compiled_authorization(self):
        result = self.service.get_job(self.job['id'])
        self.assertTrue(result['can_check_recovery'])
        for private in ('artifact_path', 'original_usb_path', 'original_version_raw', 'sha256'):
            self.assertNotIn(private, result)
        with patch('eightbitdo.qualifications.SOURCE_REVIEWED_AUTHORIZATIONS', ()):
            self.assertFalse(self.service.get_job(self.job['id'])['can_check_recovery'])
        self.discovery.assert_not_called()
        self.open_boot.assert_not_called()

    def test_check_is_read_only_and_never_makes_job_installable(self):
        self.boot.pending()
        with patch('eightbitdo.service.finish_recovery') as reset:
            result = self.service.check_recovery(self.job['id'])
        self.assertTrue(result['recovery_ready'])
        self.assertEqual(result['recovery_action'], 'finish-pending')
        self.assertFalse(result['can_install'])
        self.assertEqual(set(self.boot.commands), {5, 8})
        reset.assert_not_called()
        self.assertEqual(self.open_boot.call_args.args[0].usb_path, self.job['original_usb_path'])

    def test_check_holds_job_and_physical_update_locks(self):
        def inspected(*args, **kwargs):
            for key in (self.job['id'], 'update-' + self.job['device_id']):
                with self.assertRaises(RuntimeError):
                    with self.service.store.lock(key):
                        self.fail('Recovery check released its lock')
            from eightbitdo.recovery import RecoveryStatus
            return RecoveryStatus('return-installed', 'Fixture')
        with patch('eightbitdo.service.inspect_recovery', side_effect=inspected):
            self.service.check_recovery(self.job['id'])

    def test_wrong_family_missing_source_or_changed_evidence_denied_before_device_io(self):
        cases = [dict(model_id='ultimate-bluetooth-receiver'), dict(model_id='ultimate2-controller'),
                 dict(original_version_raw=None), dict(original_version_raw=102),
                 dict(original_usb_path='/sys/devices/other/1-2'), dict(evidence='Unreviewed'),
                 dict(evidence_level='hardware-validated'), dict(state='ready'), dict(worker_action='prepare')]
        for change in cases:
            with self.subTest(change=change):
                job = dict(self.job, **change)
                self.service.store.save(job)
                with self.assertRaises(ValueError):
                    self.service.check_recovery(job['id'])
        self.discovery.assert_not_called()
        self.open_boot.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_cache_or_record_changes_deny_recovery_before_device_io(self):
        with patch('eightbitdo.qualifications.SOURCE_REVIEWED_AUTHORIZATIONS', ()):
            with self.assertRaisesRegex(ValueError, 'authorization'):
                self.service.check_recovery(self.job['id'])
        Path(self.job['artifact_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.check_recovery(self.job['id'])
        self.discovery.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_outside_cache_and_symlink_are_rejected(self):
        original = Path(self.job['artifact_path'])
        outside = self.root / 'outside.dat'
        outside.write_bytes(original.read_bytes())
        link = original.parent / 'link.dat'
        link.symlink_to(original)
        for path in (outside, link):
            with self.subTest(path=path):
                self.service.store.save(dict(self.job, artifact_path=str(path)))
                with self.assertRaisesRegex(ValueError, 'outside'):
                    self.service.check_recovery(self.job['id'])
        self.discovery.assert_not_called()

    def test_access_and_original_port_are_required(self):
        self.service.recovery_access.status.return_value = {'configured': False}
        with self.assertRaisesRegex(ValueError, 'recovery access'):
            self.service.check_recovery(self.job['id'])
        self.discovery.assert_not_called()
        self.service.recovery_access.status.return_value = {'configured': True}
        self.device.usb_path = '/sys/devices/other/1-2'
        with self.assertRaisesRegex(ValueError, 'original receiver'):
            self.service.check_recovery(self.job['id'])
        self.open_boot.assert_not_called()

    def test_runtime_check_reports_target_or_unchanged_source_without_reset(self):
        self.discovery.return_value = [self.runtime]
        for version, state, action in ((106, 'complete', 'target-confirmed'), (101, 'blocked', 'source-unchanged')):
            with self.subTest(version=version):
                self.service.store.save(self.job)
                with patch('eightbitdo.platform.HidrawTransport', return_value=MagicMock()), \
                        patch('eightbitdo.service.query_identity', return_value=DeviceIdentity(0x6013, version)):
                    result = self.service.check_recovery(self.job['id'])
                self.assertEqual(result['state'], state)
                self.assertEqual(result['recovery_action'], action)
                self.assertFalse(result['recovery_ready'])
                self.assertFalse(result['can_install'])
        self.open_boot.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_explicit_confirmation_and_successful_check_are_both_required(self):
        with patch.object(self.service, '_spawn') as spawn:
            for confirmed in (False, None, 1, 'yes'):
                with self.subTest(confirmed=confirmed), self.assertRaisesRegex(ValueError, 'confirm'):
                    self.service.recover_update(self.job['id'], confirmed=confirmed)
            with self.assertRaisesRegex(ValueError, 'successful recovery check'):
                self.service.recover_update(self.job['id'], confirmed=True)
        spawn.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_worker_rechecks_cache_and_authorization_before_hardware(self):
        queued = self.queue()
        self.boot.sent.clear()
        self.discovery.reset_mock()
        with patch('eightbitdo.qualifications.SOURCE_REVIEWED_AUTHORIZATIONS', ()):
            with self.assertRaisesRegex(ValueError, 'authorization'):
                self.service.run_recover(queued)
        Path(queued['artifact_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.run_recover(queued)
        self.discovery.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_pending_recovery_requires_target_version_after_reset(self):
        self.boot.pending()
        queued = self.queue()
        self.boot.sent.clear()
        self.discovery.side_effect = [[self.device], [self.runtime]]
        with patch('eightbitdo.platform.HidrawTransport', return_value=MagicMock()), \
                patch('eightbitdo.service.query_identity', return_value=DeviceIdentity(0x6013, 106)):
            self.service.run_recover(queued)
        self.assertEqual(self.boot.commands.count(7), 1)
        result = self.service.get_job(queued['id'])
        self.assertEqual(result['state'], 'complete')
        self.assertIn('reports firmware 1.06', result['message'])

    def test_return_to_original_firmware_is_not_update_success(self):
        queued = self.queue()
        self.discovery.side_effect = [[self.device], [self.runtime]]
        with patch('eightbitdo.platform.HidrawTransport', return_value=MagicMock()), \
                patch('eightbitdo.service.query_identity', return_value=DeviceIdentity(0x6013, 101)):
            self.service.run_recover(queued)
        result = self.service.get_job(queued['id'])
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(result['recovery_action'], 'source-unchanged')
        self.assertIn('not completed', result['message'])
        self.assertFalse(result['can_install'])

    def test_reset_without_expected_reconnect_never_completes_or_retries(self):
        self.boot.pending()
        queued = self.queue()
        self.discovery.side_effect = [[self.device], [self.runtime]]
        with patch('eightbitdo.platform.HidrawTransport', return_value=MagicMock()), \
                patch('eightbitdo.service.query_identity', return_value=DeviceIdentity(0x6013, 101)), \
                patch('eightbitdo.service.time.monotonic', side_effect=[0, 0, 31]), \
                patch('eightbitdo.service.time.sleep'):
            self.service.run_recover(queued)
        self.assertEqual(self.boot.commands.count(7), 1)
        self.assertEqual(queued['state'], 'blocked')
        self.assertIn('not confirmed', queued['message'])
        self.assertFalse(queued['recovery_ready'])

    def test_worker_holds_update_lock_through_reconnect(self):
        queued = self.queue()
        self.discovery.side_effect = [[self.device], [self.runtime]]
        def identity(*args, **kwargs):
            with self.assertRaises(RuntimeError):
                with self.service.store.lock('update-' + queued['device_id']):
                    self.fail('Recovery released update lock too early')
            return DeviceIdentity(0x6013, 101)
        with patch('eightbitdo.platform.HidrawTransport', return_value=MagicMock()), \
                patch('eightbitdo.service.query_identity', side_effect=identity):
            self.service.run_recover(queued)

    def test_recovery_worker_requires_suspend_inhibitor(self):
        for backend in ('hid', 'wireless'):
            self.job['backend'] = backend
            with self.subTest(backend=backend), \
                    patch('eightbitdo.platform.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
                    patch('eightbitdo.platform.user_session_environment', side_effect=lambda env: env), \
                    patch('eightbitdo.platform.subprocess.Popen') as start:
                self.service._spawn(self.job, 'recover')
                command = start.call_args.args[0]
                self.assertEqual(command[0], '/usr/bin/systemd-run')
                self.assertIn('--user', command)
                self.assertIn('/usr/bin/systemd-inhibit', command)
                self.assertTrue(any(value.startswith('--setenv=PYTHONPATH=') for value in command))
                self.assertEqual(command[-1], 'recover')
                self.assertIn('--what=sleep', command)
        with patch('eightbitdo.platform.shutil.which', return_value=None), \
                patch('eightbitdo.platform.subprocess.Popen') as start:
            with self.assertRaises(RuntimeError):
                self.service._spawn(self.job, 'recover')
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
