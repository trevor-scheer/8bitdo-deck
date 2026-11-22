import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from eightbitdo.hidapi_backend import HidapiTransport, discover_hidapi, inspect_hidapi
from eightbitdo.protocol import IDENTIFY_REPORT, build_command, DeviceIdentity


DESCRIPTOR = bytes.fromhex('06 a0 ff 85 02 75 08 95 3f 81 02 85 81 91 02')


def row(**changes):
    return dict(path=b'opaque-path-with-PRIVATE-SERIAL', vendor_id=0x2DC8,
                product_id=0x6013, bus_type=1, usage_page=0xFFA0,
                product_string='8BitDo receiver', **changes)


class HidapiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hid = Mock()
        self.hid.enumerate.return_value = [row()]
        self.handle = self.hid.device.return_value
        self.handle.get_report_descriptor.return_value = DESCRIPTOR
        self.handle.set_nonblocking.return_value = 0
        self.handle.read.return_value = []
        self.handle.write.return_value = 64

    def transport(self, **kwargs):
        return HidapiTransport(row()['path'], 0x6013, self.root / 'device.lock', hid=self.hid, **kwargs)

    def test_discovery_preserves_opaque_paths_privately_and_does_not_open_devices(self):
        devices = discover_hidapi(hid=self.hid)
        self.hid.device.assert_not_called()
        self.assertEqual(devices[0].hid_paths, [row()['path']])
        self.assertEqual(devices[0].usb_path, '')
        self.assertEqual(devices[0].backend, 'hidapi')
        self.assertFalse(devices[0].can_update)
        self.assertNotIn('PRIVATE-SERIAL', json.dumps(devices[0].public()))

    def test_no_bluetooth_unknown_bus_gamepad_or_bootloader_queries(self):
        for changes in ({'bus_type': 2}, {'bus_type': 0}, {'usage_page': 1}, {'product_id': 0x3208}):
            with self.subTest(changes=changes):
                self.hid.enumerate.return_value = [row() | changes]
                device, = discover_hidapi(hid=self.hid)
                self.assertEqual(device.hid_paths, [])
                with self.assertRaises(ValueError):
                    with self.transport():
                        self.fail('Opened unsupported control interface')
                self.handle.open_path.assert_not_called()

    def test_separate_paths_are_not_merged_by_serial_number(self):
        self.hid.enumerate.return_value = [row() | {'serial_number': 'duplicate'},
                                          row() | {'path': b'second', 'serial_number': 'duplicate'}]
        self.assertEqual(len(discover_hidapi(hid=self.hid)), 2)

    def test_stale_path_or_wrong_descriptor_fails_before_output(self):
        for descriptor in (b'', b'\0' * 4097, bytes.fromhex('05 01 75 08 95 40 81 02 91 02')):
            with self.subTest(descriptor=descriptor):
                self.handle.get_report_descriptor.return_value = descriptor
                with self.assertRaises(ValueError):
                    with self.transport():
                        pass
                self.handle.write.assert_not_called()
        self.handle.get_report_descriptor.return_value = DESCRIPTOR
        self.hid.enumerate.side_effect = [[row()], []]
        with self.assertRaises(ValueError):
            with self.transport():
                pass
        self.handle.write.assert_not_called()
        self.assertTrue(self.handle.close.called)

    def test_only_exact_read_only_requests_are_accepted(self):
        with self.transport() as transport:
            transport.send(IDENTIFY_REPORT)
            transport.send(build_command(8, length=4, total_length=4))
            for report in (build_command(3), build_command(7), build_command(5, length=4),
                           build_command(8, length=4, total_length=4, offset=1), bytes(65)):
                with self.subTest(report=report), self.assertRaisesRegex(ValueError, 'read-only'):
                    transport.send(report)
            self.assertEqual(self.handle.write.call_count, 2)

    def test_queue_drain_is_bounded_and_receive_has_a_deadline(self):
        with self.transport() as transport:
            self.handle.read.return_value = [2] * 64
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                transport.send(IDENTIFY_REPORT)
            self.handle.write.assert_not_called()
            self.assertEqual(self.handle.read.call_count, 128)
            self.assertEqual(transport.receive(0.0125), bytes([2] * 64))
            self.handle.read.assert_called_with(65, 13)
            self.handle.read.return_value = []
            with self.assertRaises(TimeoutError):
                transport.receive(0.1)

    def test_short_write_stops_and_lock_is_released_after_failure(self):
        with self.transport() as transport:
            self.handle.write.return_value = 3
            with self.assertRaises(OSError):
                transport.send(IDENTIFY_REPORT)
            with self.assertRaises(RuntimeError):
                with self.transport():
                    pass
        with self.transport():
            pass

    def test_inspection_reuses_protocol_identity_validation_and_never_enables_flash(self):
        device, = discover_hidapi(hid=self.hid)
        with patch('eightbitdo.hidapi_backend._hid', return_value=self.hid), \
                patch('eightbitdo.protocol.query_identity', return_value=DeviceIdentity(0x6013, 106)), \
                patch('eightbitdo.protocol.query_paired_identity', side_effect=TimeoutError):
            inspected = inspect_hidapi(device, self.root)
        self.assertEqual(inspected.firmware_version, '1.06')
        self.assertEqual(inspected.model_id, 'ultimate2-receiver')
        self.assertFalse(inspected.can_update)
        self.assertIn('not enabled', inspected.reason)


if __name__ == '__main__':
    unittest.main()
