from contextlib import contextmanager
from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from eightbitdo import controller_recovery_service as recovery
from eightbitdo.connection import BootConnection
from eightbitdo.discovery import Device
from eightbitdo.protocol import DeviceIdentity
from eightbitdo.service import Service
from eightbitdo.wireless import WirelessAuthorization
from tests.test_controller_recovery import ControllerBoot


def device(port, pid='3208'):
    port = str(port)
    identifier = 'usb-' + hashlib.sha256(port.encode()).hexdigest()[:16]
    return Device(identifier, 'Synthetic controller', '2dc8', pid, usb_path=port, hid_paths=[Path('/dev/fake')])


class ControllerRecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.platform = Mock(native_write=True)
        self.platform.recovery_access.return_value.status.return_value = {'configured': True}
        self.service = Service(self.root, coordination_dir=self.root, catalog=Mock(), fwupd=Mock(), platform=self.platform)
        self.boot = ControllerBoot()
        self.authorization = WirelessAuthorization(self.boot.authorization, 106, self.boot.authorization.source_regions, 1)
        registry = patch('eightbitdo.qualifications.WIRELESS_AUTHORIZATIONS', (self.authorization,))
        registry.start()
        self.addCleanup(registry.stop)
        fingerprint = patch('eightbitdo.controller_recovery.REVIEWED_BOOT_SHA256', self.boot.authorization.source_regions[0].sha256)
        fingerprint.start()
        self.addCleanup(fingerprint.stop)
        self.receiver = device(self.root / 'receiver')
        self.controller = device(self.root / 'controller')
        self.devices = [self.controller]
        self.boots = {self.controller.usb_path: self.boot}
        self.platform.discover.side_effect = lambda: self.devices
        self.platform.wait_for_boot.side_effect = self.open_boot
        image = self.boot.image
        artifact = self.root / 'firmware' / 'image.dat'
        artifact.parent.mkdir()
        artifact.write_bytes(struct.pack('<7I', image.version_raw, image.address, len(image.payload), image.pid,
                                         image.reserved1, image.revision, image.reserved2) + image.payload)
        self.job = self.service.store.create('paired-' + self.receiver.id.removeprefix('usb-'), 'ultimate2-controller', 'release')
        self.job.update(state='failed', backend='wireless', worker_action='install', wireless_install_attempted=True,
                        receiver_id=self.receiver.id, original_usb_path=self.receiver.usb_path,
                        original_version='1.09', original_version_raw=109, original_hardware_revision=None,
                        receiver_version_raw=106, controller_edition=1, version='1.10',
                        artifact_path=str(artifact), sha256=image.sha256,
                        evidence_level=self.boot.authorization.evidence_level, evidence=self.boot.authorization.evidence)
        self.service.store.save(self.job)

    @contextmanager
    def open_boot(self, target, lock_dir, *, timeout=15):
        self.assertEqual(timeout, 2.0)
        self.assertEqual(lock_dir, self.root / 'locks')
        yield BootConnection(self.boots[target.usb_path], 0x3208)

    def check(self):
        with self.service.store.lock(self.job['id']):
            recovery.check_recovery(self.service, self.job)
        return self.job

    def queued(self):
        self.check()
        self.job.update(state='queued', worker_action='recover', recovery_confirmed=True, recovery_ready=False)
        self.service.store.save(self.job)
        return self.job

    def recover(self, identity=None):
        if identity is not None:
            self.platform.wait_for_application.return_value = (self.controller, identity)
        with self.service.store.lock(self.job['id']):
            recovery.run_recover(self.service, self.job)

    def test_context_is_hardware_free_and_rejects_unstarted_or_changed_policy(self):
        image, authorization = recovery.recovery_context(self.service, self.job)
        self.assertEqual(image.sha256, self.boot.image.sha256)
        self.assertEqual(authorization, self.authorization)
        for fields in ({'wireless_install_attempted': False}, {'worker_action': 'prepare'}, {'state': 'ready'},
                       {'original_version_raw': 110}, {'controller_edition': 2}, {'receiver_version_raw': 101},
                       {'evidence': 'Changed'}, {'receiver_id': self.controller.id}, {'original_version': '1.08'},
                       {'device_id': 'paired-invalid'}, {'original_usb_path': str(self.root / '..' / 'receiver')}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                recovery.recovery_context(self.service, dict(self.job, **fields))
        with patch('eightbitdo.qualifications.WIRELESS_AUTHORIZATIONS', ()):
            with self.assertRaisesRegex(ValueError, 'authorization'):
                recovery.recovery_context(self.service, self.job)
        self.platform.discover.assert_not_called()
        self.platform.wait_for_boot.assert_not_called()

    def test_context_rejects_cache_changes_and_outside_or_symlink(self):
        original = Path(self.job['artifact_path'])
        outside = self.root / 'outside.dat'
        outside.write_bytes(original.read_bytes())
        link = original.parent / 'link.dat'
        link.symlink_to(original)
        for artifact in (outside, link):
            with self.assertRaisesRegex(ValueError, 'outside'):
                recovery.recovery_context(self.service, dict(self.job, artifact_path=str(artifact)))
        original.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            recovery.recovery_context(self.service, self.job)
        self.assertEqual(self.boot.sent, [])

    def test_check_records_controller_port_separately_from_receiver_without_mutation(self):
        self.check()
        self.assertEqual(self.job['recovery_device_id'], self.controller.id)
        self.assertEqual(self.job['recovery_usb_path'], self.controller.usb_path)
        self.assertNotEqual(self.job['original_usb_path'], self.job['recovery_usb_path'])
        self.assertEqual(self.job['recovery_action'], 'return-installed')
        self.assertEqual(self.job['recovery_expected_version_raw'], 109)
        self.assertTrue(self.job['recovery_ready'])
        self.assertFalse(self.job['can_install'])
        self.assertEqual(set(self.boot.commands), {5, 8})
        self.assertEqual(self.service.store.read(self.job['id'])['recovery_usb_path'], self.controller.usb_path)

    def test_receiver_boot_is_identified_and_excluded_without_controller_reads(self):
        receiver_boot = ControllerBoot()
        receiver_boot.pid = 0x6013
        self.devices.insert(0, self.receiver)
        self.boots[self.receiver.usb_path] = receiver_boot
        self.check()
        self.assertEqual(receiver_boot.commands, [8])
        self.assertEqual(self.job['recovery_device_id'], self.controller.id)

    def test_multiple_controllers_are_rejected_before_fingerprinting(self):
        other = device(self.root / 'other-controller')
        self.devices.append(other)
        self.boots[other.usb_path] = ControllerBoot()
        with self.assertRaisesRegex(ValueError, 'only the selected controller'):
            self.check()
        self.assertEqual(self.boot.commands, [8])
        self.assertEqual(self.boots[other.usb_path].commands, [8])
        self.assertFalse(self.job['recovery_ready'])
        self.assertNotIn('recovery_usb_path', self.job)

    def test_unknown_or_unresponsive_candidate_does_not_get_ignored(self):
        other = device(self.root / 'unresponsive')
        self.devices.append(other)
        self.boots[other.usb_path] = ControllerBoot()
        self.boots[other.usb_path].timeout = True
        with self.assertRaises(TimeoutError):
            self.check()
        self.assertNotIn('recovery_usb_path', self.job)
        self.assertTrue(set(self.boot.commands) <= {8})

    def test_no_controller_and_missing_access_never_changes_mode(self):
        self.devices.clear()
        with self.assertRaisesRegex(ValueError, 'Connect only'):
            self.check()
        self.platform.recovery_access.return_value.status.return_value = {'configured': False}
        with self.assertRaisesRegex(ValueError, 'access'):
            self.check()
        self.assertEqual(self.boot.sent, [])

    def test_bad_application_or_invalid_metadata_offer_explicit_reflash_only(self):
        for corrupt_metadata in (False, True):
            self.boot.memory[0x5000] ^= 1
            if corrupt_metadata:
                self.boot.memory[0x31000] = 0
            self.check()
            self.assertEqual(self.job['recovery_action'], 'reflash')
            self.assertTrue(self.job['recovery_ready'])
            self.assertEqual(self.job['recovery_expected_version_raw'], 110)
            self.assertEqual(set(self.boot.commands), {5, 8})

    def test_pending_target_is_validated_and_returned_as_copy_action(self):
        self.boot.pending()
        self.check()
        self.assertEqual(self.job['recovery_action'], 'finish-pending')
        self.assertEqual(self.job['recovery_expected_version_raw'], 110)
        self.boot.memory[0x32000] ^= 1
        with self.assertRaises(ValueError):
            self.check()
        self.assertFalse(self.job['recovery_ready'])
        self.assertEqual(set(self.boot.commands), {5, 8})

    def test_worker_requires_confirmation_and_rechecks_recorded_context(self):
        self.queued()
        self.boot.sent.clear()
        for changes in ({'recovery_confirmed': False}, {'worker_action': 'install'},
                        {'recovery_expected_version_raw': 108}, {'recovery_action': 'unsupported'},
                        {'recovery_device_id': self.receiver.id}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                recovery.run_recover(self.service, dict(self.job, **changes))
        with patch('eightbitdo.qualifications.WIRELESS_AUTHORIZATIONS', ()):
            with self.assertRaises(ValueError):
                self.recover()
        self.assertEqual(self.boot.sent, [])

    def test_worker_never_rebinds_to_another_controller_port(self):
        self.queued()
        self.boot.sent.clear()
        other = device(self.root / 'different-port')
        self.devices[:] = [other]
        self.boots[other.usb_path] = self.boot
        with self.assertRaisesRegex(ValueError, 'same controller'):
            self.recover()
        self.assertEqual(self.boot.sent, [])

    def test_worker_refuses_changed_boot_target_without_mutation(self):
        self.queued()
        self.boot.sent.clear()
        self.boot.pid = 0x6013
        self.recover()
        self.assertEqual(self.boot.commands, [8])
        self.assertEqual(self.job['state'], 'blocked')
        self.assertFalse(self.job['recovery_ready'])

    def test_valid_source_is_reset_without_flashing_and_reported_incomplete(self):
        self.queued()
        self.boot.sent.clear()
        self.recover(DeviceIdentity(0x6012, 109))
        self.assertEqual(self.boot.commands.count(7), 1)
        self.assertTrue(set(self.boot.commands) <= {5, 7, 8})
        self.assertEqual(self.job['state'], 'blocked')
        self.assertEqual(self.job['recovery_action'], 'source-unchanged')
        self.assertEqual(self.platform.wait_for_application.call_args.kwargs['expected_version_raw'], 109)

    def test_verified_target_is_reset_without_flashing_and_completes(self):
        self.boot.memory[0x5000:0x5300] = self.boot.image.payload
        self.queued()
        self.recover(DeviceIdentity(0x6012, 110))
        self.assertEqual(self.job['state'], 'complete')
        self.assertEqual(self.job['recovery_action'], 'target-confirmed')
        self.assertNotIn(4, self.boot.commands)

    def test_reflash_worker_uses_exact_image_and_holds_all_locks_through_reconnect(self):
        self.boot.memory[0x5000] ^= 1
        self.queued()
        def reconnected(*args, **kwargs):
            for key in (self.job['id'], 'update-' + self.receiver.id, 'update-' + self.controller.id):
                with self.assertRaises(RuntimeError), self.service.store.lock(key):
                    pass
            self.assertEqual(args[0].usb_path, self.controller.usb_path)
            self.assertEqual(kwargs['expected_product_id'], 0x6012)
            self.assertEqual(kwargs['expected_version_raw'], 110)
            return self.controller, DeviceIdentity(0x6012, 110)
        self.platform.wait_for_application.side_effect = reconnected
        self.recover()
        self.assertIn(4, self.boot.commands)
        self.assertIn(3, self.boot.commands)
        self.assertEqual(self.boot.commands.count(7), 1)
        self.assertEqual(self.job['state'], 'complete')
        self.assertEqual(bytes(self.boot.memory[0x5000:0x5300]), self.boot.image.payload)

    def test_failed_durable_save_before_erase_sends_no_mutation(self):
        self.boot.memory[0x5000] ^= 1
        self.queued()
        self.boot.sent.clear()
        real_save = self.service.store.save
        def save(job):
            if job['state'] == 'installing':
                raise OSError('Disk full')
            return real_save(job)
        with patch.object(self.service.store, 'save', side_effect=save):
            self.recover()
        self.assertEqual(set(self.boot.commands), {5, 8})
        self.assertEqual(self.job['state'], 'blocked')

    def test_changed_recovery_state_requires_new_check_and_confirmation(self):
        self.queued()
        self.boot.memory[0x5000] ^= 1
        self.boot.sent.clear()
        self.recover()
        self.assertEqual(set(self.boot.commands), {5, 8})
        self.assertEqual(self.job['state'], 'blocked')
        self.assertIn('state changed', self.job['message'])

    def test_timeout_or_wrong_reconnect_never_completes_or_retries(self):
        for outcome in (TimeoutError('No reconnect'), (self.controller, DeviceIdentity(0x6012, 110))):
            with self.subTest(outcome=outcome):
                self.job.update(state='failed', worker_action='install')
                self.queued()
                self.boot.sent.clear()
                if isinstance(outcome, Exception):
                    self.platform.wait_for_application.side_effect = outcome
                else:
                    self.platform.wait_for_application.side_effect = None
                    self.platform.wait_for_application.return_value = outcome
                self.recover()
                self.assertEqual(self.boot.commands.count(7), 1)
                self.assertEqual(self.job['state'], 'blocked')
                self.assertFalse(self.job['recovery_ready'])
                self.assertFalse(self.job['recovery_confirmed'])

    def test_runtime_return_before_worker_does_not_change_mode_or_reset(self):
        self.queued()
        self.boot.sent.clear()
        self.devices[0] = replace(self.controller, pid='6012')
        self.platform.open_hid.return_value = MagicMock()
        with patch.object(recovery, 'query_identity', return_value=DeviceIdentity(0x6012, 110)):
            self.recover()
        self.assertEqual(self.job['state'], 'complete')
        self.assertEqual(self.boot.sent, [])

    def test_controller_can_replace_receiver_on_same_physical_usb_port(self):
        self.devices[:] = [self.receiver]
        self.boots[self.receiver.usb_path] = self.boot
        self.queued()
        self.assertEqual(self.job['recovery_usb_path'], self.job['original_usb_path'])
        self.assertEqual(self.job['recovery_device_id'], self.job['receiver_id'])
        self.recover(DeviceIdentity(0x6012, 109))
        self.assertEqual(self.job['recovery_action'], 'source-unchanged')
        self.assertEqual(self.boot.commands.count(7), 1)

    def test_shared_runtime_pid_queries_actual_controller_target(self):
        self.queued()
        self.boot.sent.clear()
        self.devices[0] = replace(self.controller, pid='310b')
        self.platform.open_hid.return_value = MagicMock()
        with patch.object(recovery, 'query_identity', return_value=DeviceIdentity(0x6012, 110)) as query:
            self.recover()
        self.assertEqual(query.call_args.kwargs['usb_pid'], 0x310B)
        self.assertEqual(self.job['state'], 'complete')
        self.assertEqual(self.boot.sent, [])

    def test_shared_runtime_receiver_pid_cannot_impersonate_controller(self):
        self.queued()
        self.boot.sent.clear()
        self.devices[0] = replace(self.controller, pid='310b')
        self.platform.open_hid.return_value = MagicMock()
        with patch.object(recovery, 'query_identity', return_value=DeviceIdentity(0x6013, 106)):
            with self.assertRaises(ValueError):
                self.recover()
        self.assertEqual(self.boot.sent, [])

    def test_wrong_controller_boot_code_is_rejected_before_ready(self):
        self.boot.memory[0x4000] ^= 1
        with self.assertRaises(ValueError):
            self.check()
        self.assertFalse(self.job['recovery_ready'])
        self.assertNotIn('recovery_usb_path', self.job)

    def test_service_public_context_hides_ports_and_queue_requires_fresh_confirmation(self):
        public = self.service.get_job(self.job['id'])
        self.assertTrue(public['can_check_recovery'])
        with patch.object(self.service, '_spawn') as spawn:
            with self.assertRaisesRegex(ValueError, 'successful recovery check'):
                self.service.recover_update(self.job['id'], confirmed=True)
            checked = self.service.check_recovery(self.job['id'])
            self.assertTrue(checked['recovery_ready'])
            for private in ('original_usb_path', 'recovery_usb_path', 'recovery_device_id', 'sha256'):
                self.assertNotIn(private, checked)
            for confirmation in (False, None, 1, 'yes'):
                with self.assertRaisesRegex(ValueError, 'confirm'):
                    self.service.recover_update(self.job['id'], confirmed=confirmation)
            spawn.assert_not_called()
            queued = self.service.recover_update(self.job['id'], confirmed=True)
            self.assertEqual(queued['state'], 'queued')
            self.assertEqual(spawn.call_args.args[1], 'recover')
        self.assertTrue(set(self.boot.commands) <= {5, 8})


if __name__ == '__main__':
    unittest.main()
