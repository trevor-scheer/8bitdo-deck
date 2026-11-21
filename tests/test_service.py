import hashlib
import json
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from eightbitdo.catalog import Artifact, Release
from eightbitdo.discovery import Device
from eightbitdo.jobs import JobStore
from eightbitdo.service import Service
from eightbitdo.protocol import DeviceIdentity, FlashQualification, parse_firmware
from eightbitdo.qualifications import qualification_for


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.release = Release(95, "release1", "1.06", "receiver.dat", "https://download.8bitdo.com/receiver.dat", 32, "0" * 32, "2026-11-21", "Release notes", False)
        self.image = struct.pack("<7I", 106, 0x6000, 4, 0x6013, 0, 0, 0) + b"test"
        self.catalog = Mock()
        self.catalog.list_releases.return_value = [self.release]
        self.fwupd = Mock()
        self.fwupd.devices.return_value = []
        self.service = Service(self.root, catalog=self.catalog, fwupd=self.fwupd)
        self.image_path = self.root / "image.dat"
        self.image_path.write_bytes(self.image)
        self.catalog.download.return_value = Artifact(str(self.image_path), hashlib.sha256(self.image).hexdigest(), self.release, False, "No verified vendor signature.")

    def job(self):
        return self.service.store.create("", "ultimate2-receiver", "release1")

    def test_download_validates_but_does_not_qualify_native_flash(self):
        job = self.job()
        self.service.run_prepare(job)
        result = self.service.get_job(job["id"])
        self.assertEqual(result["state"], "blocked")
        self.assertFalse(result["can_install"])
        self.assertIn("No verified vendor signature", result["message"])
        self.assertNotIn("artifact_path", result)
        with self.assertRaises(ValueError):
            self.service.install_update(job["id"])
        self.fwupd.install.assert_not_called()

    def test_container_mismatch_never_becomes_installable(self):
        wrong = struct.pack("<7I", 106, 0x6000, 4, 0x6012, 0, 0, 0) + b"test"
        self.image_path.write_bytes(wrong)
        self.catalog.download.return_value = replace(self.catalog.download.return_value, sha256=hashlib.sha256(wrong).hexdigest())
        job = self.job()
        with self.assertRaisesRegex(ValueError, "product ID"):
            self.service.run_prepare(job)
        self.assertFalse(self.service.store.read(job["id"])["can_install"])

    def test_version_mismatch_rejected(self):
        self.catalog.list_releases.return_value = [replace(self.release, version="1.07")]
        with self.assertRaisesRegex(ValueError, "version"):
            self.service.run_prepare(self.job())

    def test_changed_cache_rejected(self):
        self.image_path.write_bytes(self.image + b"modified")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.run_prepare(self.job())

    def test_manual_selection_cannot_override_live_identity(self):
        device = Device("usb-" + "a" * 16, "Receiver", "2dc8", "6013", model_id="ultimate2-receiver", status="identified")
        with patch.object(self.service, "_devices", return_value=([device], [])), patch.object(self.service, "_spawn") as spawn:
            result = self.service.prepare_update(device.id, "ultimate2-controller", "release1")
        self.assertIn("identity", result["error"])
        spawn.assert_not_called()

    def test_unknown_device_cannot_prepare(self):
        with patch.object(self.service, "_devices", return_value=([], [])), patch.object(self.service, "_spawn") as spawn:
            result = self.service.prepare_update("usb-" + "a" * 16, "ultimate2-receiver", "release1")
        self.assertIn("error", result)
        spawn.assert_not_called()

    def test_manual_download_has_no_hardware_target(self):
        with patch.object(self.service, "_spawn") as spawn:
            result = self.service.prepare_update("", "ultimate2-receiver", "release1")
        self.assertIn("job_id", result)
        self.assertEqual(spawn.call_args.args[0]["device_id"], "")
        self.assertFalse(spawn.call_args.args[0]["can_install"])

    def test_release_and_job_path_traversal_rejected(self):
        self.assertIn("error", self.service.prepare_update("", "ultimate2-receiver", "../../image"))
        with self.assertRaises(ValueError):
            self.service.get_job("../../image")

    def test_fwupd_ready_job_requires_matching_trusted_release(self):
        device = Device("fwupd-" + "a" * 40, "SN30 Pro", "2dc8", "fwupd", can_update=True, backend="fwupd")
        self.fwupd.require_device.return_value = device
        self.fwupd.releases.return_value = [{"id": "release1", "version": "2.00", "downloadable": True}]
        job = self.service.store.create(device.id, device.id, "release1")
        self.service.run_prepare(job)
        result = self.service.get_job(job["id"])
        self.assertTrue(result["can_install"])
        self.assertEqual(result["target_label"], "SN30 Pro → 2.00")
        self.fwupd.install.assert_not_called()
        with patch.object(self.service, "_spawn") as spawn:
            self.service.install_update(job["id"])
            self.assertEqual(spawn.call_args.args[1], "install")

    def test_untrusted_fwupd_release_cannot_be_prepared(self):
        device = Device("fwupd-" + "a" * 40, "SN30 Pro", "2dc8", "fwupd", can_update=True)
        self.fwupd.require_device.return_value = device
        self.fwupd.releases.return_value = [{"id": "release1", "version": "2.00", "downloadable": False}]
        job = self.service.store.create(device.id, device.id, "release1")
        with self.assertRaises(ValueError):
            self.service.run_prepare(job)
        self.fwupd.install.assert_not_called()

    def test_success_requires_reported_version(self):
        device_id = "fwupd-" + "a" * 40
        job = self.service.store.create(device_id, device_id, "release1")
        job.update(backend="fwupd", version="2.00")
        self.fwupd.devices.return_value = [Device(device_id, "SN30 Pro", "2dc8", "fwupd", firmware_version="2.00")]
        self.service.run_install(job)
        self.assertEqual(self.service.get_job(job["id"])["state"], "complete")

    def test_install_failure_is_never_retried(self):
        job = self.service.store.create("fwupd-" + "a" * 40, "fwupd-" + "a" * 40, "release1")
        job.update(backend="fwupd", version="2.00")
        self.fwupd.install.side_effect = RuntimeError("device failed")
        with self.assertRaises(RuntimeError):
            self.service.run_install(job)
        self.fwupd.install.assert_called_once()
        self.assertNotEqual(self.service.get_job(job["id"])["state"], "complete")

    def test_diagnostics_omit_identifiers_paths_and_raw_errors(self):
        device = Device("fwupd-secret", "private serial number", "2dc8", "6013", model_id="fwupd-secret", usb_path="/private/usb")
        with patch.object(self.service, "_devices", return_value=([device], ["/home/private/cache error"])):
            result = self.service.export_diagnostics()
        contents = Path(result["path"]).read_text()
        for private in ("secret", "private", "/home", "usb_path"):
            self.assertNotIn(private, contents)
        self.assertEqual(json.loads(contents)["devices"][0]["pid"], "6013")

    def test_dead_worker_becomes_failed_without_retry(self):
        job = self.job()
        job.update(started_at=0, worker_pid=999999999)
        self.service.store.save(job)
        with patch.object(self.service, "_spawn") as spawn:
            self.assertEqual(self.service.get_job(job["id"])["state"], "failed")
        spawn.assert_not_called()

    def test_job_lock_rejects_concurrent_operation(self):
        job = self.job()
        another = JobStore(self.root)
        with self.service.store.lock(job["id"]):
            with self.assertRaisesRegex(RuntimeError, "already"):
                with another.lock(job["id"]):
                    self.fail("Lock was not exclusive")

    def native_job(self):
        directory = self.root / "firmware"
        directory.mkdir(exist_ok=True)
        image_path = directory / "image.dat"
        image_path.write_bytes(self.image)
        job = self.service.store.create("usb-" + "a" * 16, "ultimate2-receiver", "release1")
        job.update(backend="hid", artifact_path=str(image_path), sha256=hashlib.sha256(self.image).hexdigest(), version="1.06", original_version="1.05")
        return job

    def test_default_qualification_registry_enables_no_native_writes(self):
        image = parse_firmware(self.image)
        self.assertIsNone(qualification_for(image, DeviceIdentity(0x6013, 105)))

    def test_native_install_requires_qualification_again(self):
        job = self.native_job()
        device = Device(job["device_id"], "Receiver", "2dc8", "6013", hid_paths=[Path("/dev/fake")])
        with patch.object(self.service, "_native_device", return_value=device), \
                patch("eightbitdo.service.HidrawTransport", return_value=MagicMock()), \
                patch("eightbitdo.service.query_identity", return_value=DeviceIdentity(0x6013, 105)), \
                patch("eightbitdo.service.flash_application") as flash:
            with self.assertRaisesRegex(ValueError, "qualification"):
                self.service.run_install(job)
        flash.assert_not_called()

    def test_native_modified_image_fails_before_device_io(self):
        job = self.native_job()
        Path(job["artifact_path"]).write_bytes(b"modified")
        with patch("eightbitdo.service.HidrawTransport") as transport:
            with self.assertRaisesRegex(ValueError, "changed"):
                self.service.run_install(job)
        transport.assert_not_called()

    def test_native_cache_path_cannot_escape_state(self):
        job = self.native_job()
        job["artifact_path"] = str(self.image_path)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.service.run_install(job)

    def test_qualified_native_job_requires_reconnect_version_check(self):
        job = self.native_job()
        device = Device(job["device_id"], "Receiver", "2dc8", "6013", firmware_version="1.06", hid_paths=[Path("/dev/fake")])
        record = FlashQualification(0x6013, None, 0, job["sha256"], "Test fixture, not hardware evidence", (105,))
        with patch.object(self.service, "_native_device", return_value=device) as rediscover, \
                patch("eightbitdo.service.HidrawTransport", return_value=MagicMock()), \
                patch("eightbitdo.service.query_identity", return_value=DeviceIdentity(0x6013, 105)), \
                patch("eightbitdo.service.qualification_for", return_value=record), \
                patch("eightbitdo.service.flash_application") as flash:
            self.service.run_install(job)
        flash.assert_called_once()
        self.assertEqual(rediscover.call_count, 2)
        self.assertEqual(self.service.get_job(job["id"])["state"], "complete")

    def test_native_worker_is_launched_under_suspend_inhibitor(self):
        job = self.native_job()
        with patch("eightbitdo.service.shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                patch("eightbitdo.service.subprocess.Popen") as spawn:
            self.service._spawn(job, "install")
        command = spawn.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/systemd-inhibit")
        self.assertIn("--what=sleep", command)
        self.assertIn("--mode=block", command)
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])

    def test_native_worker_without_inhibitor_never_starts(self):
        job = self.native_job()
        with patch("eightbitdo.service.shutil.which", return_value=None), \
                patch("eightbitdo.service.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "prevent suspend"):
                self.service._spawn(job, "install")
        spawn.assert_not_called()

    def test_qualification_binds_the_source_version(self):
        image = parse_firmware(self.image)
        record = FlashQualification(0x6013, None, 0, image.sha256, "Test fixture only", (105,))
        with patch("eightbitdo.qualifications.QUALIFICATIONS", (record,)):
            self.assertIsNotNone(qualification_for(image, DeviceIdentity(0x6013, 105)))
            self.assertIsNone(qualification_for(image, DeviceIdentity(0x6013, 104)))
            self.assertIsNone(qualification_for(image, DeviceIdentity(0x6013, 105, beta=1)))

    def test_reporting_failure_does_not_abort_a_native_transaction(self):
        job = self.native_job()
        device = Device(job["device_id"], "Receiver", "2dc8", "6013", firmware_version="1.06", hid_paths=[Path("/dev/fake")])
        record = FlashQualification(0x6013, None, 0, job["sha256"], "Test fixture only", (105,))

        def perform_flash(*args, **kwargs):
            with patch.object(self.service.store, "save", side_effect=OSError("disk full")):
                kwargs["progress"]("writing", 2, 4)
                kwargs["progress"]("verifying", 4, 4)

        with patch.object(self.service, "_native_device", return_value=device), \
                patch("eightbitdo.service.HidrawTransport", return_value=MagicMock()), \
                patch("eightbitdo.service.query_identity", return_value=DeviceIdentity(0x6013, 105)), \
                patch("eightbitdo.service.qualification_for", return_value=record), \
                patch("eightbitdo.service.flash_application", side_effect=perform_flash):
            self.service.run_install(job)
        self.assertEqual(job["state"], "complete")
        self.assertIn("reporting_error", job)

    def test_native_update_lock_covers_reconnect_verification(self):
        job = self.native_job()
        other = JobStore(self.root)

        def native_operation(active_job):
            with self.assertRaises(RuntimeError):
                with other.lock("update-" + active_job["device_id"]):
                    self.fail("Concurrent update acquired the device")

        with patch.object(self.service, "_install_native", side_effect=native_operation):
            self.service.run_install(job)


if __name__ == "__main__":
    unittest.main()
