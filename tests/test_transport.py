import unittest
from pathlib import Path
from unittest.mock import patch

from eightbitdo.transport import BootHidrawTransport, HidrawTransport


APPLICATION_DESCRIPTOR = bytes.fromhex("06 a0 ff 85 02 75 08 95 3f 81 02 85 81 91 02")
BOOT_DESCRIPTOR = bytes.fromhex("06 00 ff 75 08 95 40 81 02 91 02")


class TransportProfileTests(unittest.TestCase):
    def setUp(self):
        self.app = HidrawTransport(Path('/dev/fake'), 0x6013, Path('/tmp/unused'))
        self.boot = BootHidrawTransport(Path('/dev/fake'), 0x3208, Path('/tmp/unused'), expected_usb_path='/sys/devices/fake')

    def test_reviewed_ultimate2_boot_descriptor(self):
        descriptor = bytes.fromhex("05 8c 09 01 a1 01 09 03 15 00 26 00 ff 75 08 95 40 81 02 09 04 15 00 26 00 ff 75 08 95 40 91 02 c0")
        self.boot._check_descriptor(descriptor)

    def test_application_and_boot_interfaces_cannot_be_confused(self):
        self.app._check_descriptor(APPLICATION_DESCRIPTOR)
        self.boot._check_descriptor(BOOT_DESCRIPTOR)
        with self.assertRaises(ValueError):
            self.app._check_descriptor(BOOT_DESCRIPTOR)
        with self.assertRaises(ValueError):
            self.boot._check_descriptor(APPLICATION_DESCRIPTOR)

    def test_boot_requires_vendor_unnumbered_exact_input_and_output_size(self):
        for descriptor in (
            bytes.fromhex('05 01 75 08 95 40 81 02 91 02'),
            bytes.fromhex('06 00 ff 75 08 95 3f 81 02 91 02'),
            bytes.fromhex('06 00 ff 75 08 95 40 81 02'),
            BOOT_DESCRIPTOR + bytes.fromhex('85 01 95 01 b1 02'),
        ):
            with self.subTest(descriptor=descriptor.hex()), self.assertRaises(ValueError):
                self.boot._check_descriptor(descriptor)

    def test_boot_requires_known_usb_pid_and_original_port(self):
        for pid, port in [(0x6013, '/sys/fake'), (0xFFFF, '/sys/fake'), (0x3208, '')]:
            with self.subTest(pid=pid, port=port), self.assertRaises(ValueError):
                BootHidrawTransport(Path('/dev/fake'), pid, Path('/tmp/unused'), expected_usb_path=port)

    def test_wrong_output_framing_is_rejected_before_device_write(self):
        self.app.fd = self.boot.fd = 99
        for transport, report in (
            (self.app, bytes(65)), (self.app, bytes(64)),
            (self.boot, bytes(64)), (self.boot, b'\x81' + bytes(64)),
        ):
            with self.subTest(transport=type(transport).__name__), patch('os.write') as write:
                with self.assertRaises(ValueError):
                    transport.send(report)
                write.assert_not_called()

    def test_valid_boot_output_keeps_the_zero_selector(self):
        self.boot.fd = 99
        report = bytes.fromhex('00 05 08') + bytes(62)
        with patch('select.select', side_effect=[([], [], []), ([], [99], [])]), \
                patch('os.write', return_value=65) as write:
            self.boot.send(report)
        write.assert_called_once_with(99, report)

    def test_boot_input_keeps_the_protocol_zero_byte(self):
        self.boot.fd = 99
        report = bytes.fromhex('00 05 00 00 08 00') + bytes(58)
        with patch('select.select', return_value=([99], [], [])), patch('os.read', return_value=report):
            self.assertEqual(self.boot.receive(1), report)


if __name__ == '__main__':
    unittest.main()
