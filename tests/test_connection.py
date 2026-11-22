import sys
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from eightbitdo.connection import require_boot_access, wait_for_application, wait_for_boot
from eightbitdo.discovery import discover
from eightbitdo.protocol import DeviceIdentity, FirmwareMismatch
from eightbitdo.transport import BootHidrawTransport
from test_discovery import SysfsFixture, VENDOR_DESCRIPTOR


BOOT_DESCRIPTOR = bytes.fromhex("06 00 ff 75 08 95 40 81 02 91 02")


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux host integration")
class ConnectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fixture = SysfsFixture(self.root)
        self.port = self.fixture.usb(pid="3109")
        self.fixture.interface(self.port)
        self.original, = discover(self.fixture.sys, self.fixture.dev)
        self.lock_dir = self.root / "locks"
        self.clock = Clock()
        for target, value in (
            ("eightbitdo.connection.time.monotonic", self.clock.monotonic),
            ("eightbitdo.connection.time.sleep", self.clock.sleep),
            ("eightbitdo.connection.recovery_access_configured", lambda: True),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def boot(self, **kwargs):
        return wait_for_boot(self.original, self.lock_dir, sys_root=self.fixture.sys,
                             dev_root=self.fixture.dev, timeout=0.3, **kwargs)

    def application(self, **kwargs):
        return wait_for_application(self.original, self.lock_dir, sys_root=self.fixture.sys,
                                    dev_root=self.fixture.dev, timeout=0.3,
                                    expected_product_id=0x6008, expected_version_raw=104, **kwargs)

    def set_boot(self, descriptor=BOOT_DESCRIPTOR):
        (self.port / "idProduct").write_text("3208")
        (self.fixture.hid / "hidraw7/device/report_descriptor").write_bytes(descriptor)

    def test_permission_preflight_never_changes_mode_or_permissions(self):
        with patch("eightbitdo.connection.recovery_access_configured", return_value=False), \
                patch("eightbitdo.connection.os.access") as access:
            with self.assertRaises(PermissionError):
                require_boot_access(self.original)
            access.assert_not_called()
        with patch("eightbitdo.connection.os.access", return_value=False):
            with self.assertRaises(PermissionError):
                require_boot_access(self.original)
        with patch("eightbitdo.connection.os.access", return_value=True):
            require_boot_access(self.original)

    def test_missing_physical_port_never_scans_or_opens(self):
        original = replace(self.original, usb_path="")
        with patch("eightbitdo.connection.BootHidrawTransport") as transport:
            with self.assertRaises(ValueError), wait_for_boot(original, self.lock_dir):
                pass
            transport.assert_not_called()

    def test_boot_on_another_port_is_never_opened(self):
        other = self.fixture.usb(name="1-3", pid="3208")
        self.fixture.interface(other, name="hidraw9", descriptor=BOOT_DESCRIPTOR)
        with patch("eightbitdo.connection.BootHidrawTransport") as transport:
            with self.assertRaises(TimeoutError), self.boot():
                pass
            transport.assert_not_called()
        self.assertAlmostEqual(self.clock.now, 0.3)

    def test_runtime_or_unknown_usb_personality_cannot_be_boot(self):
        for pid in ("3109", "ffff"):
            (self.port / "idProduct").write_text(pid)
            with self.subTest(pid=pid), patch("eightbitdo.connection.BootHidrawTransport") as transport:
                with self.assertRaises(TimeoutError), self.boot():
                    pass
                transport.assert_not_called()

    def test_same_port_boot_opens_without_identity_guess_and_always_closes(self):
        self.set_boot()
        with patch("eightbitdo.connection.BootHidrawTransport") as constructor:
            transport = constructor.return_value
            with self.assertRaisesRegex(RuntimeError, "caller failure"):
                with self.boot() as connection:
                    self.assertIs(connection.transport, transport)
                    self.assertEqual(connection.usb_pid, 0x3208)
                    transport.send.assert_not_called()
                    transport.receive.assert_not_called()
                    raise RuntimeError("caller failure")
            constructor.assert_called_once_with(self.fixture.dev / "hidraw7", 0x3208,
                                                self.lock_dir / (self.original.id + ".lock"),
                                                expected_usb_path=str(self.port))
            transport.__exit__.assert_called_once()

    def test_permission_race_retries_bounded_and_reports_denial(self):
        self.set_boot()
        with patch("eightbitdo.connection.BootHidrawTransport") as constructor:
            constructor.return_value.__enter__.side_effect = PermissionError("access denied")
            with self.assertRaises(PermissionError), self.boot():
                pass
            self.assertGreater(constructor.call_count, 1)
            self.assertAlmostEqual(self.clock.now, 0.3)

    def test_boot_open_waits_for_permissions_without_sending_reports(self):
        self.set_boot()
        with patch("eightbitdo.connection.BootHidrawTransport") as constructor:
            transport = constructor.return_value
            transport.__enter__.side_effect = [PermissionError("ACL not applied yet"), transport]
            with self.boot() as connection:
                self.assertIs(connection.transport, transport)
                transport.send.assert_not_called()
                transport.receive.assert_not_called()
            self.assertEqual(constructor.call_count, 2)
            self.assertAlmostEqual(self.clock.now, 0.1)

    def test_descriptor_rejection_cannot_yield_a_boot_connection(self):
        self.set_boot(VENDOR_DESCRIPTOR)
        def open_checked(path, pid, lock, **kwargs):
            candidate = MagicMock()
            candidate.__enter__.side_effect = lambda: BootHidrawTransport(path, pid, lock, **kwargs)._check_descriptor(VENDOR_DESCRIPTOR)
            return candidate
        with patch("eightbitdo.connection.BootHidrawTransport", side_effect=open_checked):
            with self.assertRaises(TimeoutError), self.boot():
                self.fail("A numbered application descriptor was accepted as boot")

    def test_returning_application_must_have_verified_target_and_exact_version(self):
        with patch("eightbitdo.connection.HidrawTransport") as constructor, \
                patch("eightbitdo.connection.query_identity", return_value=DeviceIdentity(0x6008, 104)):
            device, identity = self.application()
            self.assertEqual((device.id, device.firmware_pid, identity.version_raw), (self.original.id, 0x6008, 104))
            self.assertEqual(constructor.call_args.kwargs["expected_usb_path"], str(self.port))
        for identity in (DeviceIdentity(0x6013, 104), DeviceIdentity(0x6008, 104, target_verified=False)):
            with self.subTest(identity=identity), patch("eightbitdo.connection.HidrawTransport"), \
                    patch("eightbitdo.connection.query_identity", return_value=identity):
                with self.assertRaises(FirmwareMismatch):
                    self.application()
        with patch("eightbitdo.connection.HidrawTransport"), \
                patch("eightbitdo.connection.query_identity", return_value=DeviceIdentity(0x6008, 103)):
            with self.assertRaises(TimeoutError):
                self.application()

    def test_application_on_another_port_cannot_complete_update(self):
        self.set_boot()
        other = self.fixture.usb(name="1-3", pid="3109")
        self.fixture.interface(other, name="hidraw9")
        with patch("eightbitdo.connection.HidrawTransport") as transport, \
                patch("eightbitdo.connection.query_identity", return_value=DeviceIdentity(0x6008, 104)) as query:
            with self.assertRaises(TimeoutError):
                self.application()
            transport.assert_not_called()
            query.assert_not_called()

    def test_invalid_wait_limits_fail_without_io(self):
        for timeout in (0, -1, 61, float("nan")):
            with self.subTest(timeout=timeout), patch("eightbitdo.connection.BootHidrawTransport") as transport:
                with self.assertRaises(ValueError), wait_for_boot(self.original, self.lock_dir, timeout=timeout):
                    pass
                transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
