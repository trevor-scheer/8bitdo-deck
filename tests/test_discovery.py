import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eightbitdo.discovery import Device, clean_text, discover, inspect_device, report_layout
from eightbitdo.protocol import DeviceIdentity, ProtocolTimeout
from eightbitdo.registry import QUERY_PIDS, get_model, model_for_identity


VENDOR_DESCRIPTOR = bytes.fromhex("06 a0 ff 85 02 75 08 95 3f 81 02 85 81 91 02")


class SysfsFixture:
    def __init__(self, root):
        self.sys = root / "sys"
        self.dev = root / "dev"
        self.bus = self.sys / "bus/usb/devices"
        self.hid = self.sys / "class/hidraw"
        self.bus.mkdir(parents=True)
        self.hid.mkdir(parents=True)

    def usb(self, name="1-2", pid="6013", vendor="2dc8", product="8BitDo Ultimate 2"):
        node = self.sys / "devices/pci0000/usb1" / name
        node.mkdir(parents=True)
        (node / "idVendor").write_text(vendor)
        (node / "idProduct").write_text(pid)
        if product is not None:
            (node / "product").write_text(product)
        (node / "serial").write_text("PRIVATE-DEVICE-SERIAL")
        (self.bus / name).symlink_to(node, target_is_directory=True)
        return node

    def interface(self, usb, name="hidraw7", descriptor=VENDOR_DESCRIPTOR):
        node = usb / (usb.name + ":1.0") / ("0003:2DC8:6013." + name)
        node.mkdir(parents=True)
        (node / "report_descriptor").write_bytes(descriptor)
        handle = self.hid / name
        handle.mkdir()
        (handle / "device").symlink_to(node, target_is_directory=True)


class DescriptorTests(unittest.TestCase):
    def test_standard_vendor_reports(self):
        self.assertEqual(report_layout(VENDOR_DESCRIPTOR), ({0xFFA0}, {2}, {0x81}))

    def test_split_report_items_are_aggregated(self):
        descriptor = bytes.fromhex("06 7a ff 85 02 75 08 95 20 81 02 95 1f 81 02 85 81 95 3f 91 02")
        self.assertEqual(report_layout(descriptor), ({0xFF7A}, {2}, {0x81}))

    def test_report_sizes_stay_separate_by_id(self):
        descriptor = bytes.fromhex("06 a0 ff 75 08 95 20 85 02 81 02 85 03 95 1f 81 02")
        self.assertEqual(report_layout(descriptor), (set(), set(), set()))

    def test_global_push_pop_restores_report_definition(self):
        descriptor = bytes.fromhex("06 a0 ff 85 02 75 08 95 3f a4 85 01 95 01 81 02 b4 81 02 85 81 91 02")
        self.assertEqual(report_layout(descriptor), ({0xFFA0}, {2}, {0x81}))

    def test_valid_long_item_is_skipped(self):
        self.assertEqual(report_layout(bytes.fromhex("fe 02 01 aa bb") + VENDOR_DESCRIPTOR), ({0xFFA0}, {2}, {0x81}))

    def test_invalid_items_fail_closed(self):
        for descriptor in [b"\x06\xa0", b"\xfe", bytes.fromhex("fe 02 01 aa"), b"\xb4", bytes.fromhex("85 00"), bytes.fromhex("86 00 01")]:
            with self.subTest(descriptor=descriptor.hex()), self.assertRaises(ValueError):
                report_layout(descriptor)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = SysfsFixture(self.root)

    def scan(self):
        return discover(self.fixture.sys, self.fixture.dev)

    def test_usb_hid_association_and_public_privacy(self):
        node = self.fixture.usb()
        self.fixture.interface(node)
        device, = self.scan()
        self.assertEqual(device.model_id, "ultimate2-receiver")
        self.assertEqual(device.hid_paths, [self.fixture.dev / "hidraw7"])
        self.assertFalse(device.can_update)
        public = json.dumps(device.public())
        self.assertNotIn("PRIVATE-DEVICE-SERIAL", public)
        self.assertNotIn(str(self.root), public)
        self.assertNotIn("hidraw7", public)
        self.assertNotIn("usb_path", public)
        self.assertNotIn("hid_paths", public)

    def test_unknown_models_are_retained_without_flash_permission(self):
        self.fixture.usb(pid="ffff", product="Unknown\n8BitDo\x00 model")
        device, = self.scan()
        self.assertEqual(device.name, "Unknown8BitDo model")
        self.assertIsNone(device.model_id)
        self.assertEqual(device.role, "unknown")
        self.assertFalse(device.can_update)

    def test_missing_product_name_has_generic_fallback(self):
        self.fixture.usb(product=None)
        device, = self.scan()
        self.assertEqual(device.name, "8BitDo device")

    def test_unrelated_vendor_and_invalid_ids_are_ignored(self):
        self.fixture.usb(name="1-1", vendor="057e")
        self.fixture.usb(name="1-3", pid="invalid")
        self.assertEqual(self.scan(), [])

    def test_malformed_descriptor_does_not_remove_usb_device(self):
        node = self.fixture.usb()
        self.fixture.interface(node, descriptor=b"\xfe")
        device, = self.scan()
        self.assertEqual(device.hid_paths, [])

    def test_gamepad_only_interface_is_not_opened_as_vendor_channel(self):
        node = self.fixture.usb()
        descriptor = bytes.fromhex("05 01 85 02 75 08 95 3f 81 02 85 81 91 02")
        self.fixture.interface(node, descriptor=descriptor)
        self.assertEqual(self.scan()[0].hid_paths, [])

    def test_empty_sysfs_is_valid(self):
        self.assertEqual(self.scan(), [])

    def test_permission_denied_does_not_attempt_hid_io(self):
        device = Device("test", "Device", "2dc8", "6013", hid_paths=[self.fixture.dev / "hidraw7"])
        with patch("eightbitdo.discovery.os.access", return_value=False), patch("eightbitdo.transport.HidrawTransport") as transport:
            result = inspect_device(device, self.root / "locks")
        transport.assert_not_called()
        self.assertEqual(result.status, "unavailable")
        self.assertIn("access denied", result.reason)

    def test_no_compatible_interface_requires_connection(self):
        device = Device("test", "Device", "2dc8", "6013")
        self.assertEqual(inspect_device(device, self.root / "locks").status, "connection_required")

    def test_unknown_pid_never_queries_hardware(self):
        device = Device("test", "Device", "2dc8", "ffff", hid_paths=[Path("/dev/hidraw7")])
        with patch("eightbitdo.transport.HidrawTransport") as transport:
            result = inspect_device(device, self.root / "locks")
        transport.assert_not_called()
        self.assertEqual(result.status, "detected")

    def test_query_maps_verified_target_but_does_not_enable_writes(self):
        device = Device("test", "Device", "2dc8", "6013", hid_paths=[Path("/dev/hidraw7")])
        with patch("eightbitdo.discovery.os.access", return_value=True), patch("eightbitdo.transport.HidrawTransport"), patch("eightbitdo.protocol.query_identity", return_value=DeviceIdentity(0x6013, 106)):
            result = inspect_device(device, self.root / "locks")
        self.assertEqual(result.model_id, "ultimate2-receiver")
        self.assertEqual(result.firmware_version, "1.06")
        self.assertEqual(result.role, "receiver")
        self.assertFalse(result.can_update)

    def test_query_timeout_is_reported_without_device_loss(self):
        device = Device("test", "Device", "2dc8", "6013", hid_paths=[Path("/dev/hidraw7")])
        with patch("eightbitdo.discovery.os.access", return_value=True), patch("eightbitdo.transport.HidrawTransport"), patch("eightbitdo.protocol.query_identity", side_effect=ProtocolTimeout("No reply")):
            result = inspect_device(device, self.root / "locks")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "No reply")


class RegistryTests(unittest.TestCase):
    def test_runtime_alias_is_not_a_firmware_target(self):
        self.assertIsNone(model_for_identity(0x3109))
        self.assertNotEqual(get_model("ultimate2-controller").firmware_pid, get_model("ultimate2-receiver").firmware_pid)

    def test_all_enabled_queries_have_protocol_implementations(self):
        from eightbitdo.protocol import IDENTIFY_PIDS
        self.assertLessEqual(QUERY_PIDS, IDENTIFY_PIDS)

    def test_unknown_model_is_rejected(self):
        for identifier in ["unknown", "../../firmware", None, []]:
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                get_model(identifier)

    def test_clean_text_bounds_device_supplied_strings(self):
        self.assertEqual(clean_text("x\n\0y"), "xy")
        self.assertEqual(len(clean_text("x" * 1000)), 160)


if __name__ == "__main__":
    unittest.main()
