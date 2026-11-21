import hashlib
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from eightbitdo.connection import BootConnection
from eightbitdo.discovery import Device
from eightbitdo.legacy_protocol import LegacySourceReviewedAuthorization
from eightbitdo.protocol import DeviceIdentity, crc16
from eightbitdo.service import Service
from tests.test_legacy_protocol import INITIALIZATION, ReceiverDevice, QueueTransport, firmware, reply


class LegacyRecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = Service(self.root, catalog=Mock(), fwupd=Mock())
        self.service.recovery_access.status = Mock(return_value={'configured': True})
        self.image = firmware()
        self.boot = ReceiverDevice(self.image)
        self.authorization = LegacySourceReviewedAuthorization(
            0x6008, None, 0, self.image.sha256, 'Synthetic interrupted update fixture', (103,),
            initialization=INITIALIZATION,
            boot_prefix_sha256=hashlib.sha256(self.image.payload[:0x4000]).hexdigest(),
            boot_prefix_crc16=crc16(self.image.payload[:0x4000]),
        )
        registry = patch('eightbitdo.qualifications.LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS', (self.authorization,))
        registry.start()
        self.addCleanup(registry.stop)
        artifact = self.root / 'firmware' / 'image.dat'
        artifact.parent.mkdir()
        artifact.write_bytes(struct.pack('<7I', 104, 0x3000, len(self.image.payload), 0x6008, 0, 0, 0) + self.image.payload)
        port = '/sys/devices/fake/1-1'
        device_id = 'usb-' + hashlib.sha256(port.encode()).hexdigest()[:16]
        self.job = self.service.store.create(device_id, 'ultimate-bluetooth-receiver', 'release1')
        self.job.update(state='failed', worker_action='install', backend='hid', original_usb_path=port,
                        original_version_raw=103, original_version='1.03', original_hardware_revision=None,
                        version='1.04', artifact_path=str(artifact), sha256=self.image.sha256,
                        evidence_level=self.authorization.evidence_level, evidence=self.authorization.evidence)
        self.service.store.save(self.job)
        self.device = Device(device_id, 'Receiver', '2dc8', '3208', usb_path=port)
        self.runtime = Device(device_id, 'Receiver', '2dc8', '3109', usb_path=port, hid_paths=[Path('/dev/fake')])
        self.discovery = self.start_patch('eightbitdo.service.discover', return_value=[self.device])
        self.context = MagicMock()
        self.context.__enter__.return_value = BootConnection(self.boot, 0x3208)
        self.open_boot = self.start_patch('eightbitdo.service.wait_for_boot', return_value=self.context)

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def queue(self):
        checked = self.service.check_recovery(self.job['id'])
        self.assertEqual(checked['recovery_action'], 'reflash')
        def spawn(job, action):
            job['worker_action'] = action
            self.service.store.save(job)
        with patch.object(self.service, '_spawn', side_effect=spawn):
            self.service.recover_update(self.job['id'], confirmed=True)
        return self.service.store.read(self.job['id'])

    def test_check_reads_target_and_initialization_without_writes_or_mode_change(self):
        with patch('eightbitdo.service.request_receiver_boot') as enter, \
                patch('eightbitdo.service.flash_boot_receiver') as flash:
            result = self.service.check_recovery(self.job['id'])
        self.assertEqual(self.boot.commands, [8, 0xC1])
        self.assertTrue(result['can_check_recovery'])
        self.assertTrue(result['recovery_ready'])
        self.assertEqual(result['recovery_action'], 'reflash')
        self.assertFalse(result['can_install'])
        self.assertIn('erasing and reinstalling', result['message'])
        enter.assert_not_called()
        flash.assert_not_called()

    def test_check_rejects_wrong_firmware_target_before_initialization(self):
        foreign = QueueTransport([reply(8, b'\x13\x60')])
        self.context.__enter__.return_value = BootConnection(foreign, 0x3208)
        with self.assertRaisesRegex(ValueError, 'another|target'):
            self.service.check_recovery(self.job['id'])
        self.assertEqual(len(foreign.sent), 1)
        self.assertFalse(self.service.get_job(self.job['id'])['recovery_ready'])

    def test_check_rejects_geometry_and_unobserved_boot_usb_personality(self):
        self.boot.initialization = replace(INITIALIZATION, upgrade_erase_offset=4096)
        with self.assertRaisesRegex(ValueError, 'initialization'):
            self.service.check_recovery(self.job['id'])
        self.assertEqual(self.boot.commands, [8, 0xC1])
        self.boot.sent.clear()
        self.context.__enter__.return_value = BootConnection(self.boot, 0x3002)
        with self.assertRaisesRegex(ValueError, 'USB personality'):
            self.service.check_recovery(self.job['id'])
        self.assertEqual(self.boot.sent, [])

    def test_only_attempted_source_bound_jobs_can_check_recovery(self):
        for changed in (dict(worker_action='prepare'), dict(original_version_raw=None),
                        dict(original_version_raw=102, original_version='1.02'),
                        dict(evidence='Changed'), dict(model_id='ultimate2-controller'),
                        dict(original_usb_path='/sys/devices/foreign')):
            with self.subTest(changed=changed):
                self.service.store.save(dict(self.job, **changed))
                with self.assertRaises(ValueError):
                    self.service.check_recovery(self.job['id'])
        self.discovery.assert_not_called()
        self.open_boot.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_unavailable_record_or_changed_cache_fails_before_hardware(self):
        with patch('eightbitdo.qualifications.LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS', ()):
            self.assertFalse(self.service.get_job(self.job['id'])['can_check_recovery'])
            with self.assertRaisesRegex(ValueError, 'authorization'):
                self.service.check_recovery(self.job['id'])
        Path(self.job['artifact_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.check_recovery(self.job['id'])
        self.discovery.assert_not_called()

    def test_other_physical_port_and_missing_recovery_access_are_rejected(self):
        self.service.recovery_access.status.return_value = {'configured': False}
        with self.assertRaisesRegex(ValueError, 'recovery access'):
            self.service.check_recovery(self.job['id'])
        self.discovery.assert_not_called()
        self.service.recovery_access.status.return_value = {'configured': True}
        self.device.usb_path = '/sys/devices/fake/1-2'
        with self.assertRaisesRegex(ValueError, 'original receiver'):
            self.service.check_recovery(self.job['id'])
        self.open_boot.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_explicit_reflash_confirmation_requires_successful_check(self):
        with patch.object(self.service, '_spawn') as spawn:
            with self.assertRaisesRegex(ValueError, 'confirm'):
                self.service.recover_update(self.job['id'])
            with self.assertRaisesRegex(ValueError, 'successful recovery check'):
                self.service.recover_update(self.job['id'], confirmed=True)
            job = dict(self.job, recovery_ready=True, recovery_action='return-installed')
            self.service.store.save(job)
            with self.assertRaisesRegex(ValueError, 'successful recovery check'):
                self.service.recover_update(self.job['id'], confirmed=True)
        spawn.assert_not_called()

    def test_confirmed_worker_rechecks_cached_image_and_authorization(self):
        queued = self.queue()
        self.discovery.reset_mock()
        self.boot.sent.clear()
        with patch('eightbitdo.qualifications.LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS', ()):
            with self.assertRaisesRegex(ValueError, 'authorization'):
                self.service.run_recover(queued)
        Path(queued['artifact_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.run_recover(queued)
        self.discovery.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_confirmed_worker_rechecks_live_initialization_before_erasing(self):
        queued = self.queue()
        self.boot.sent.clear()
        self.boot.initialization = replace(INITIALIZATION, erase_size=256)
        with self.assertRaisesRegex(ValueError, 'initialization'):
            self.service.run_recover(queued)
        self.assertEqual(self.boot.commands, [8, 0xC1])

    def test_reflash_uses_exact_cached_image_and_verifies_target_under_update_lock(self):
        queued = self.queue()
        self.boot.sent.clear()
        def returning(device, locks, **kwargs):
            self.assertEqual(device.usb_path, queued['original_usb_path'])
            self.assertEqual(kwargs, dict(expected_product_id=0x6008, expected_version_raw=104))
            with self.assertRaises(RuntimeError):
                with self.service.store.lock('update-' + queued['device_id']):
                    self.fail('Update lock released before version verification')
            return self.runtime, DeviceIdentity(0x6008, 104)
        with patch('eightbitdo.service.wait_for_application', side_effect=returning), \
                patch('eightbitdo.service.request_receiver_boot') as enter:
            self.service.run_recover(queued)
        self.assertEqual(self.boot.memory, self.image.payload[0x4000:])
        self.assertEqual(self.boot.commands[:2], [8, 0xC1])
        self.assertEqual(self.boot.commands.count(0xC1), 1)
        self.assertEqual(self.boot.commands.count(7), 1)
        self.assertEqual(self.boot.commands.count(0xC2), 1)
        self.assertEqual(self.service.get_job(queued['id'])['state'], 'complete')
        enter.assert_not_called()

    def test_transfer_without_verified_reconnect_is_not_success_or_retry(self):
        queued = self.queue()
        self.boot.sent.clear()
        with patch('eightbitdo.service.wait_for_application', side_effect=TimeoutError):
            self.service.run_recover(queued)
        self.assertEqual(queued['state'], 'blocked')
        self.assertIn('not confirmed', queued['message'])
        self.assertFalse(queued['recovery_ready'])
        self.assertEqual(self.boot.commands.count(7), 1)

    def test_runtime_check_never_reflashes_original_and_can_confirm_target(self):
        self.discovery.return_value = [self.runtime]
        for version, state in ((103, 'blocked'), (104, 'complete')):
            with self.subTest(version=version):
                self.service.store.save(self.job)
                with patch('eightbitdo.service.HidrawTransport', return_value=MagicMock()), \
                        patch('eightbitdo.service.query_identity', return_value=DeviceIdentity(0x6008, version)):
                    result = self.service.check_recovery(self.job['id'])
                self.assertEqual(result['state'], state)
                self.assertFalse(result['recovery_ready'])
        self.open_boot.assert_not_called()
        self.assertEqual(self.boot.sent, [])

    def test_runtime_return_before_worker_does_not_trigger_another_boot_transition(self):
        queued = self.queue()
        self.boot.sent.clear()
        self.open_boot.reset_mock()
        self.discovery.return_value = [self.runtime]
        with patch('eightbitdo.service.HidrawTransport', return_value=MagicMock()), \
                patch('eightbitdo.service.query_identity', return_value=DeviceIdentity(0x6008, 103)), \
                patch('eightbitdo.service.request_receiver_boot') as enter:
            self.service.run_recover(queued)
        self.assertEqual(queued['state'], 'blocked')
        self.assertEqual(queued['recovery_action'], 'source-unchanged')
        enter.assert_not_called()
        self.open_boot.assert_not_called()
        self.assertEqual(self.boot.sent, [])


if __name__ == '__main__':
    unittest.main()
