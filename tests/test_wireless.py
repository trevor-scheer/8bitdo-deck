import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eightbitdo.discovery import Device
from eightbitdo.protocol import FirmwareMismatch, ProtocolError, ProtocolTimeout, UnsupportedProtocol
from eightbitdo.service import Service
from eightbitdo.wireless import query_wireless_target
from test_protocol import QueueTransport, identity_report, product_reply


def status_report(version=110, beta=0, edition=1):
    return (b"\x02\x04" + struct.pack("<HHHHII", 4, 0x121, 6, 0, 0, 0)
            + bytes([100]) + struct.pack("<H", version) + bytes([beta, 0, edition])).ljust(64, b"\0")


def preflight(receiver_version=106, controller_version=110):
    return [product_reply(), identity_report(version=receiver_version), status_report(controller_version)]


class WirelessTests(unittest.TestCase):
    def test_radio_query_reaches_controller_without_starting_update(self):
        transport = QueueTransport(preflight() + [product_reply(0x6012), status_report()])
        target = query_wireless_target(transport, usb_pid=0x310B)
        self.assertEqual(target.receiver.pid, 0x6013)
        self.assertEqual(target.controller.pid, 0x6012)
        self.assertEqual(target.controller.version, "1.10")
        self.assertEqual(target.status.battery_percent, 100)
        self.assertEqual(len(transport.sent), 5)
        self.assertEqual([report[1] for report in transport.sent], [5, 5, 4, 6, 4])
        self.assertEqual(transport.sent[3], bytes.fromhex(
            "81 06 08 00 00 00 04 00 00 00 04 00 00 00 00 00 00 00"
        ).ljust(64, b"\0"))

    def test_unknown_receiver_version_never_sends_radio_command(self):
        transport = QueueTransport(preflight(receiver_version=101))
        with self.assertRaises(UnsupportedProtocol):
            query_wireless_target(transport, usb_pid=0x310B)
        self.assertEqual(len(transport.sent), 2)
        self.assertNotIn(6, [r[1] for r in transport.sent])

    def test_direct_controller_on_shared_usb_alias_is_rejected(self):
        transport = QueueTransport([product_reply(0x6012), identity_report(version=110)])
        with self.assertRaises(FirmwareMismatch):
            query_wireless_target(transport, usb_pid=0x310B)
        self.assertEqual(len(transport.sent), 2)

    def test_unreviewed_controller_version_or_beta_never_sends_radio_command(self):
        for status in (status_report(version=109), status_report(beta=1)):
            transport = QueueTransport([product_reply(), identity_report(), status])
            with self.subTest(status=status), self.assertRaises(UnsupportedProtocol):
                query_wireless_target(transport, usb_pid=0x6013)
            self.assertEqual(len(transport.sent), 3)

    def test_receiver_reply_cannot_be_mistaken_for_wireless_controller(self):
        transport = QueueTransport(preflight() + [product_reply(0x6013)])
        with self.assertRaises(FirmwareMismatch):
            query_wireless_target(transport, usb_pid=0x310B)
        self.assertEqual(len(transport.sent), 4)

    def test_wrong_command_status_length_or_offset_fails_without_retry(self):
        for field, value in ((2, 1), (4, 3), (6, 4), (10, 4), (14, 1)):
            reply = bytearray(product_reply(0x6012))
            struct.pack_into("<H", reply, field, value)
            transport = QueueTransport(preflight() + [bytes(reply)])
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                query_wireless_target(transport, usb_pid=0x310B)
            self.assertEqual(len(transport.sent), 4)

    def test_radio_timeout_does_not_initialize_or_retry(self):
        transport = QueueTransport(preflight())
        with self.assertRaises(ProtocolTimeout):
            query_wireless_target(transport, usb_pid=0x310B)
        self.assertEqual(len(transport.sent), 4)

    def test_controller_change_after_radio_query_is_rejected(self):
        transport = QueueTransport(preflight() + [product_reply(0x6012), status_report(edition=2)])
        with self.assertRaises(FirmwareMismatch):
            query_wireless_target(transport, usb_pid=0x310B)

    def test_invalid_input_sends_nothing(self):
        for kwargs in ({"usb_pid": 0x3109}, {"usb_pid": 0x310B, "timeout": 0}):
            transport = QueueTransport([])
            with self.subTest(kwargs=kwargs), self.assertRaises(ProtocolError):
                query_wireless_target(transport, **kwargs)
            self.assertEqual(transport.sent, [])

    def test_service_probe_never_qualifies_installation_or_exposes_paths(self):
        device = Device("usb-test", "Receiver", "2dc8", "310b", hid_paths=[Path("/dev/hidraw7")], usb_path="/private/usb")
        transport = QueueTransport(preflight() + [product_reply(0x6012), status_report()])
        target = query_wireless_target(transport, usb_pid=0x310B)
        with tempfile.TemporaryDirectory() as temporary, \
                patch("eightbitdo.platform.discover", return_value=[device]), \
                patch("eightbitdo.platform.HidrawTransport"), \
                patch("eightbitdo.wireless.query_wireless_target", return_value=target):
            result = Service(Path(temporary)).probe_wireless()["devices"][0]
        self.assertEqual(result["controller_pid"], "6012")
        self.assertFalse(result["can_update"])
        self.assertNotIn("usb_path", result)
        self.assertNotIn("hid_paths", result)


if __name__ == "__main__":
    unittest.main()
