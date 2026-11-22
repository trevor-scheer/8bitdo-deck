import hashlib
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from eightbitdo.catalog import Artifact, Release
from eightbitdo.discovery import Device
from eightbitdo.protocol import FirmwareMismatch, parse_firmware
from eightbitdo.service import Service
from eightbitdo.wireless_service import paired_devices, wait_for_wireless_application
from test_protocol import firmware
from test_wireless_flash import RadioDevice


class WirelessServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.radio = RadioDevice()
        self.radio.controller_version = 109
        data = bytearray(firmware(pid=0x6012, size=4231))
        struct.pack_into("<I", data, 0, 110)
        self.radio.image = parse_firmware(bytes(data))
        self.radio.authorization = replace(self.radio.authorization, controller=replace(
            self.radio.authorization.controller, source_versions=(109,), image_sha256=self.radio.image.sha256,
        ))
        policy = patch("eightbitdo.qualifications.WIRELESS_AUTHORIZATIONS", (self.radio.authorization,))
        policy.start()
        self.addCleanup(policy.stop)
        port = str(self.root / "usb1" / "1-1")
        digest = hashlib.sha256(port.encode()).hexdigest()[:16]
        self.receiver = Device("usb-" + digest, "Receiver", "2dc8", "310b", role="receiver",
                               model_id="ultimate2-receiver", status="identified", firmware_version="1.06",
                               hid_paths=[Path("/dev/fake")], usb_path=port, paired_firmware_version="1.09")
        self.paired_id = "paired-" + digest
        platform = Mock(native_write=True)
        platform.discover.return_value = [self.receiver]
        platform.inspect_device.side_effect = lambda device, _: device
        platform.open_hid.side_effect = lambda *args, **kwargs: nullcontext(self.radio)
        platform.recovery_access.return_value.status.return_value = {"configured": True}
        self.release = Release(94, "reviewed-release", "1.10", "controller.dat", "https://download.8bitdo.com/controller.dat",
                               len(data), "0" * 32, "2026-11-22", "Controller update", False)
        self.image_path = self.root / "firmware" / "controller.dat"
        self.image_path.parent.mkdir()
        self.image_path.write_bytes(data)
        catalog = Mock()
        catalog.list_releases.return_value = [self.release]
        catalog.download.return_value = Artifact(str(self.image_path), self.radio.image.sha256, self.release,
                                                False, "No verified vendor signature.")
        fwupd = Mock()
        fwupd.devices.return_value = []
        self.service = Service(self.root, coordination_dir=self.root, platform=platform, catalog=catalog, fwupd=fwupd)

    def job(self):
        return self.service.store.create(self.paired_id, "ultimate2-controller", self.release.key)

    def prepared(self):
        job = self.job()
        self.service.run_prepare(job)
        self.assertEqual(job["state"], "ready")
        return job

    def test_scan_promotes_only_verified_radio_identity(self):
        devices = self.service.scan_devices()["devices"]
        paired = next(device for device in devices if device["id"] == self.paired_id)
        self.assertEqual(paired["status"], "identified")
        self.assertEqual(paired["backend"], "wireless")
        self.assertEqual(paired["firmware_version"], "1.09")
        self.assertNotIn("usb_path", paired)
        self.assertTrue(set(self.radio.radio_commands) <= {8})

    def test_status_without_radio_identity_remains_unavailable_for_preparation(self):
        self.radio.controller_version = 108
        device = paired_devices(self.service, [self.receiver])[0]
        self.assertEqual(device.status, "wireless")
        self.assertFalse(device.can_update)
        with patch.object(self.service, "_spawn") as spawn:
            result = self.service.prepare_update(self.paired_id, "ultimate2-controller", self.release.key)
        self.assertIn("error", result)
        spawn.assert_not_called()

    def test_non_writing_platform_does_not_probe_radio(self):
        self.service.platform.native_write = False
        device = paired_devices(self.service, [self.receiver])[0]
        self.assertEqual(device.status, "wireless")
        self.service.platform.open_hid.assert_not_called()
        self.assertTrue(all(model["download_only"] for model in self.service.get_models()))

    def test_preparation_retains_receiver_binding_and_checks_sources_without_mutation(self):
        job = self.prepared()
        self.assertTrue(job["can_install"])
        self.assertEqual(job["backend"], "wireless")
        self.assertEqual(job["receiver_id"], self.receiver.id)
        self.assertEqual(job["original_usb_path"], self.receiver.usb_path)
        self.assertEqual(job["original_version_raw"], 109)
        self.assertEqual(job["receiver_version_raw"], 106)
        self.assertEqual(job["controller_edition"], 1)
        self.assertTrue(set(self.radio.radio_commands) <= {5, 8})

    def test_current_controller_is_not_reflashed(self):
        self.radio.controller_version = 110
        job = self.job()
        self.service.run_prepare(job)
        self.assertFalse(job["can_install"])
        self.assertIn("already has the selected firmware 1.10", job["message"])
        self.assertNotIn(0xC1, self.radio.radio_commands)

    def test_no_qualification_never_initializes(self):
        job = self.job()
        with patch("eightbitdo.qualifications.WIRELESS_AUTHORIZATIONS", ()):
            self.service.run_prepare(job)
        self.assertFalse(job["can_install"])
        self.assertIn("not supported", job["message"])
        self.assertNotIn(0xC1, self.radio.radio_commands)

    def test_missing_recovery_access_never_initializes(self):
        self.service.recovery_access.status.return_value = {"configured": False}
        job = self.job()
        self.service.run_prepare(job)
        self.assertFalse(job["can_install"])
        self.assertIn("USB recovery access", job["message"])
        self.assertNotIn(0xC1, self.radio.radio_commands)

    def test_direct_usb_controller_and_manual_model_remain_download_only(self):
        job = self.service.store.create("", "ultimate2-controller", self.release.key)
        self.service.run_prepare(job)
        self.assertFalse(job["can_install"])
        self.assertIn("2.4 GHz", job["message"])
        model = next(model for model in self.service.get_models() if model["id"] == "ultimate2-controller")
        self.assertFalse(model["download_only"])
        self.assertTrue(model["wireless_only"])

    def test_paired_id_cannot_select_receiver_firmware(self):
        with patch.object(self.service, "_spawn") as spawn:
            result = self.service.prepare_update(self.paired_id, "ultimate2-receiver", self.release.key)
        self.assertIn("error", result)
        spawn.assert_not_called()

    def test_queue_and_worker_share_receiver_update_lock(self):
        job = self.prepared()
        with patch.object(self.service, "_spawn") as spawn:
            self.service.install_update(job["id"])
        spawn.assert_called_once()
        with patch.object(self.service.store, "lock", return_value=nullcontext()) as lock, \
                patch("eightbitdo.wireless_service.wait_for_wireless_application"):
            self.service.run_install(job)
        lock.assert_called_once_with("update-" + self.receiver.id)
        self.assertEqual(job["state"], "complete")
        self.assertEqual(self.radio.radio_commands[-2:], [0xC4, 7])
        self.assertTrue(set(self.radio.receiver.commands) <= {5, 8, 0x2100})

    def test_changed_cache_port_source_or_evidence_cannot_write(self):
        for field in ("cache", "port", "source", "evidence"):
            with self.subTest(field=field):
                job = self.prepared()
                original_data = self.image_path.read_bytes()
                if field == "cache":
                    self.image_path.write_bytes(original_data + b"changed")
                elif field == "port":
                    job["original_usb_path"] += "-moved"
                elif field == "source":
                    job["original_version_raw"] = 108
                else:
                    job["evidence"] = "changed"
                with self.assertRaises((ValueError, FirmwareMismatch)):
                    self.service.run_install(job)
                self.assertNotIn(0xC1, self.radio.radio_commands)
                self.image_path.write_bytes(original_data)

    def test_reconnect_timeout_does_not_report_success_or_retry_flash(self):
        job = self.prepared()
        with patch("eightbitdo.wireless_service.wait_for_wireless_application", side_effect=TimeoutError):
            self.service.run_install(job)
        self.assertEqual(job["state"], "blocked")
        self.assertFalse(job["can_install"])
        self.assertEqual(self.radio.radio_commands.count(0xC4), 1)

    def test_reconnect_uses_same_receiver_and_requires_target_version(self):
        from eightbitdo.wireless import query_wireless_target
        target = query_wireless_target(self.radio, usb_pid=0x310B)
        self.radio.controller_version = 110
        returned = wait_for_wireless_application(self.service, self.receiver, target, self.radio.image, timeout=0.1)
        self.assertEqual(returned.controller.version_raw, 110)
        self.radio.edition = 2
        with self.assertRaises(FirmwareMismatch):
            wait_for_wireless_application(self.service, self.receiver, target, self.radio.image, timeout=0.1)
        self.radio.edition = 1
        self.service.platform.discover.return_value = [replace(self.receiver, usb_path="/sys/different")]
        self.service.platform.open_hid.reset_mock()
        with self.assertRaises(TimeoutError):
            wait_for_wireless_application(self.service, self.receiver, target, self.radio.image, timeout=0.01)
        self.service.platform.open_hid.assert_not_called()


if __name__ == "__main__":
    unittest.main()
