import struct
import unittest
from dataclasses import replace

from eightbitdo.protocol import (
    DeviceIdentity, FirmwareMismatch, HEADER, IDENTIFY_REPORT, ProtocolError, ProtocolTimeout,
    UnsupportedProtocol, build_command, crc16, parse_firmware,
)
from eightbitdo.wireless import (
    WirelessAuthorization, WirelessTransport, flash_wireless_controller,
    verify_wireless_sources,
)
from test_protocol import firmware, source_review_fixture
from test_wireless import status_report


class RadioDevice:
    """Separate controller and receiver flash; routing mistakes cannot pass silently."""

    def __init__(self, *, target_size=4231):
        _, self.receiver, receiver_source = source_review_fixture(0x6013)
        _, self.controller, controller_source = source_review_fixture(0x6012)
        self.controller.memory.extend(b"\0" * 8192)
        self.image = parse_firmware(firmware(pid=0x6012, size=target_size))
        self.authorization = WirelessAuthorization(
            replace(controller_source, image_sha256=self.image.sha256, source_versions=(110,)),
            106, receiver_source.source_regions, 1,
        )
        self.sent = []
        self.reply = None
        self.battery = 100
        self.edition = 1
        self.controller_version = 110
        self.timeout_command = None
        self.init_header = (0, 0xC1, 0, 0, 0, 0)
        self.corrupt_earlier_block = False

    def send(self, report):
        self.sent.append(report)
        command = HEADER.unpack_from(report, 2)[0]
        if report[1] == 4:
            reply = bytearray(status_report(self.controller_version, edition=self.edition))
            reply[18] = self.battery
            self.reply = bytes(reply)
        elif report[1] == 5:
            self.receiver.send(report)
            self.reply = self.receiver.reply
        elif report[1] == 6:
            self.controller.send(report[:1] + b"\x05" + report[2:])
            self.reply = self.controller.reply
            if command == 0xC1:
                # A real radio C1 has no geometry payload. Use no padding at all
                # to catch accidental reuse of the USB initialization parser.
                self.reply = b"\x02\x05" + HEADER.pack(*self.init_header)
            if self.corrupt_earlier_block and command == 3 and HEADER.unpack_from(report, 2)[5] == 4096:
                self.controller.memory[0x32000] ^= 1
            if command == self.timeout_command:
                self.reply = None
        else:
            raise AssertionError("Unexpected class")

    def receive(self, timeout):
        if self.reply is None:
            raise TimeoutError("Simulated radio disconnect")
        return self.reply

    @property
    def radio_commands(self):
        return [HEADER.unpack_from(report, 2)[0] for report in self.sent if report[1] == 6]

    def flash(self, **kwargs):
        return flash_wireless_controller(self, self.image, usb_pid=0x310B,
                                         authorization=self.authorization, **kwargs)


class WirelessFlashTests(unittest.TestCase):
    def test_complete_multiblock_transfer_only_mutates_controller(self):
        device = RadioDevice()
        receiver_before = bytes(device.receiver.memory)
        result = device.flash()
        self.assertEqual(result.product_id, 0x6012)
        self.assertEqual(result.bytes_verified, len(device.image.payload))
        self.assertTrue(result.needs_reconnect_verification)
        self.assertEqual(device.controller.memory[0x32000:0x32000 + len(device.image.payload)], device.image.payload)
        self.assertEqual(bytes(device.receiver.memory), receiver_before)
        self.assertTrue(set(device.receiver.commands) <= {5, 8, 0x2100})
        self.assertEqual(device.radio_commands.count(0xC1), 1)
        self.assertEqual(device.radio_commands.count(4), 2)
        self.assertEqual(device.radio_commands.count(0xC3), 4)
        self.assertEqual(device.radio_commands[-2:], [0xC4, 7])

    def test_preparation_reads_only_and_preserves_all_flash(self):
        device = RadioDevice()
        before = bytes(device.receiver.memory), bytes(device.controller.memory)
        target = verify_wireless_sources(device, device.image, usb_pid=0x6013, authorization=device.authorization)
        self.assertEqual(target.controller, DeviceIdentity(0x6012, 110))
        self.assertTrue(set(device.radio_commands) <= {5, 8})
        self.assertEqual((bytes(device.receiver.memory), bytes(device.controller.memory)), before)

    def test_reviewed_109_source_can_use_its_own_exact_fingerprint(self):
        device = RadioDevice()
        device.controller_version = 109
        device.authorization = replace(device.authorization, controller=replace(device.authorization.controller, source_versions=(109,)))
        device.flash()
        self.assertEqual(device.radio_commands[-2:], [0xC4, 7])

    def test_unknown_or_low_battery_wrong_edition_stop_before_initialization(self):
        for field, value in (("battery", 255), ("battery", 29), ("edition", 2)):
            with self.subTest(field=field, value=value):
                device = RadioDevice()
                setattr(device, field, value)
                with self.assertRaises(ProtocolError):
                    device.flash()
                self.assertTrue(set(device.radio_commands) <= {8})

    def test_both_receiver_and_controller_sources_are_checked_again_at_install(self):
        for component, address in (("receiver", 0x4FFF), ("receiver", 0x505F),
                                   ("controller", 0x4FFF), ("controller", 0x505F)):
            with self.subTest(component=component, address=address):
                device = RadioDevice()
                verify_wireless_sources(device, device.image, usb_pid=0x310B, authorization=device.authorization)
                device.sent.clear()
                getattr(device, component).memory[address] ^= 1
                with self.assertRaisesRegex(FirmwareMismatch, "fingerprint"):
                    device.flash()
                self.assertTrue(set(device.radio_commands) <= {5, 8})

    def test_wrong_image_or_receiver_policy_sends_nothing(self):
        device = RadioDevice()
        for authorization in (
            replace(device.authorization, receiver_version_raw=101),
            replace(device.authorization, controller=replace(device.authorization.controller, image_sha256="0" * 64)),
            replace(device.authorization, receiver_source_regions=()),
            replace(device.authorization, minimum_battery_percent=0),
        ):
            with self.subTest(authorization=authorization), self.assertRaises(ProtocolError):
                flash_wireless_controller(device, device.image, usb_pid=0x310B, authorization=authorization)
            self.assertEqual(device.sent, [])

    def test_pending_controller_metadata_stops_before_initialization(self):
        device = RadioDevice()
        metadata = bytearray(52)
        struct.pack_into("<I", metadata, 24, 0xAEB14D6C)
        struct.pack_into("<I", metadata, 48, crc16(metadata[:48]))
        device.controller.metadata = bytes(metadata)
        with self.assertRaisesRegex(ProtocolError, "already pending"):
            device.flash()
        self.assertNotIn(0xC1, device.radio_commands)

    def test_bad_initialization_ack_never_erases(self):
        for index, value in ((0, 1), (1, 4), (2, 16), (3, 1), (4, 16), (5, 4096)):
            device = RadioDevice()
            header = list(device.init_header)
            header[index] = value
            device.init_header = tuple(header)
            with self.subTest(index=index), self.assertRaisesRegex(ProtocolError, "initialization"):
                device.flash()
            self.assertNotIn(4, device.radio_commands)
            self.assertNotIn(0xC4, device.radio_commands)

    def test_radio_loss_never_retries_or_sends_later_commit_reset(self):
        for command in (0xC1, 4, 3, 0xC3, 0xC4):
            device = RadioDevice()
            device.timeout_command = command
            with self.subTest(command=command), self.assertRaises(ProtocolTimeout):
                device.flash()
            self.assertEqual(device.radio_commands.count(command), 1)
            self.assertNotIn(7, device.radio_commands)
            if command != 0xC4:
                self.assertNotIn(0xC4, device.radio_commands)

    def test_checksum_readback_and_ack_failures_never_commit(self):
        for attribute, value in (("bad_crc", True), ("bad_sum", True), ("bad_readback", True),
                                 ("wrong_offset", True), ("fail_command", 3), ("fail_command", 4)):
            device = RadioDevice()
            setattr(device.controller, attribute, value)
            with self.subTest(attribute=attribute, value=value), self.assertRaises(ProtocolError):
                device.flash()
            self.assertNotIn(0xC4, device.radio_commands)
            self.assertNotIn(7, device.radio_commands)

    def test_all_blocks_are_verified_again_before_commit(self):
        device = RadioDevice()
        device.corrupt_earlier_block = True
        with self.assertRaisesRegex(ProtocolError, "verification failed"):
            device.flash()
        self.assertEqual(device.radio_commands.count(4), 2)
        self.assertNotIn(0xC4, device.radio_commands)

    def test_prepared_target_mismatch_does_not_initialize(self):
        device = RadioDevice()
        target = verify_wireless_sources(device, device.image, usb_pid=0x310B, authorization=device.authorization)
        expected = replace(target, controller=replace(target.controller, version_raw=109))
        with self.assertRaisesRegex(FirmwareMismatch, "prepared controller"):
            device.flash(expected_target=expected)
        self.assertNotIn(0xC1, device.radio_commands)

    def test_transport_rejects_usb_identity_and_read_only_side_effects(self):
        device = RadioDevice()
        radio = WirelessTransport(device)
        for report in (IDENTIFY_REPORT, build_command(0xC1), build_command(0xC3), build_command(7),
                       build_command(5, length=32, total_length=32, offset=0x5DFFF)):
            with self.subTest(report=report[:6]), self.assertRaises(UnsupportedProtocol):
                radio.send(report)
        self.assertEqual(device.sent, [])


if __name__ == "__main__":
    unittest.main()
