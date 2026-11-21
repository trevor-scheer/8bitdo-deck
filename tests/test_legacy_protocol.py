import errno
import hashlib
import struct
import unittest
from dataclasses import replace

from eightbitdo.legacy_protocol import (
    BOOT_ENTRY_REPORT, LegacySourceReviewedAuthorization, ReceiverInitialization, build_boot_command,
    flash_boot_receiver, parse_boot_reply, plan_receiver_flash,
    query_boot_target, request_receiver_boot, reset_receiver_boot, validate_receiver_authorization,
)
from eightbitdo.protocol import (
    DeviceIdentity, FirmwareMismatch, FlashAuthorization, FlashQualification, ProtocolError,
    ProtocolTimeout, UnsupportedProtocol, crc16, parse_firmware,
)


INITIALIZATION = ReceiverInitialization(0x4000, 0xFFFFFFFF, 0, 4096)
IDENTITY = DeviceIdentity(0x6008, 103)


def firmware():
    # Synthetic bytes, not vendor firmware; tests do not qualify any hardware.
    payload = bytearray(0x4000)
    payload[16:22] = b"80NA_R"
    payload += b"".join(bytes([value]) * 4096 for value in (17, 34, 51, 68))
    return parse_firmware(struct.pack("<7I", 104, 0x3000, len(payload), 0x6008, 0, 0, 0) + payload)


def qualification(image):
    return FlashQualification(0x6008, None, 0, image.sha256, "offline static-derived fixture only", (103,))


def reply(command, data=b"", status=0):
    # Static native evidence establishes only status, command, and payload.
    # Deliberately non-echo header fields prevent assuming modern ACK semantics.
    return (b"\0\x05" + struct.pack("<HHHHII", status, command, 9, 11, 13, 15) + data).ljust(64, b"\0")


class QueueTransport:
    def __init__(self, reports=()):
        self.reports = list(reports)
        self.sent = []

    def send(self, report):
        self.sent.append(report)

    def receive(self, timeout):
        if not self.reports:
            raise TimeoutError()
        report = self.reports.pop(0)
        if isinstance(report, Exception):
            raise report
        return report


class ReceiverDevice:
    """Model the bootloader's buffered first page and receiver-relative offsets."""

    def __init__(self, image, *, fail=None, bad_crc_page=None, initialization=INITIALIZATION,
                 timeout_command=None):
        self.expected = image.payload[INITIALIZATION.image_offset:]
        self.memory = bytearray(len(self.expected))
        self.header_buffer = bytearray(4096)
        self.sent = []
        self.pending = None
        self.fail = fail
        self.bad_crc_page = bad_crc_page
        self.initialization = initialization
        self.timeout_command = timeout_command
        self.saved = False
        self.received_commands = []

    @property
    def commands(self):
        return [struct.unpack_from("<H", report, 2)[0] for report in self.sent]

    def send(self, report):
        assert len(report) == 65 and report[:2] == b"\0\x05"
        self.sent.append(report)
        command, params, length, checksum, total, offset = struct.unpack_from("<HHHHII", report, 2)
        data = report[18:18 + length]
        assert params == 0
        if command == 8:
            assert (length, total, offset, checksum) == (2, 2, 0, 0xFFFF)
            self.pending = reply(8, b"\x08\x60")
            return
        assert checksum == crc16(data)
        if command == self.fail:
            self.pending = reply(command, status=1)
            return
        response_data = b""
        if command == 0xC1:
            response_data = struct.pack("<4I", *self.initialization.__dict__.values())
        elif command == 4:
            assert total == 4096 and length == 0 and offset % 4096 == 0
            assert 3 not in self.commands
            self.memory[offset:offset + total] = b"\xff" * total
        elif command == 3:
            assert total == len(self.expected) and length == 32
            assert self.commands.count(4) == len(self.expected) // 4096
            if offset < 4096:
                self.header_buffer[offset:offset + length] = data
            else:
                self.memory[offset:offset + length] = data
        elif command == 0xC3:
            start, count, reserved = struct.unpack("<III", data)
            assert (length, total, offset, count, reserved) == (12, 12, 0, 4096, 0)
            assert start >= 8192 or self.saved
            checked = self.memory[start:start + count]
            value = crc16(checked) ^ int(start // 4096 == self.bad_crc_page)
            response_data = struct.pack("<H", value)
        elif command == 0xC2:
            assert self.commands.count(0xC3) == len(self.expected) // 4096 - 2
            assert self.memory[:4096] == b"\xff" * 4096
            self.memory[:4096] = self.header_buffer
            self.saved = True
        elif command == 7:
            assert self.saved and self.memory == self.expected
            assert self.commands.count(0xC3) == len(self.expected) // 4096
        self.pending = reply(command, response_data)

    def receive(self, timeout):
        self.received_commands.append(self.commands[-1])
        if self.commands[-1] == self.timeout_command:
            raise TimeoutError()
        return self.pending


class LegacyProtocolTests(unittest.TestCase):
    def test_native_query_reset_and_initialization_wire_fixtures(self):
        fixtures = (
            (build_boot_command(8, length=2, total_length=2), "0005080000000200ffff0200000000000000"),
            (build_boot_command(7), "0005070000000000ffff0000000000000000"),
            (build_boot_command(0xC1), "0005c10000000000ffff0000000000000000"),
            (build_boot_command(4, total_length=4096, offset=8192), "0005040000000000ffff0010000000200000"),
        )
        for packet, prefix in fixtures:
            self.assertEqual(packet, bytes.fromhex(prefix).ljust(65, b"\0"))
        packet = build_boot_command(3, data=b"123456789", total_length=4096, offset=32)
        self.assertEqual(packet[:18], bytes.fromhex("0005030000000900374b0010000020000000"))
        self.assertEqual(packet[18:27], b"123456789")

    def test_boot_response_preserves_initial_zero_and_does_not_require_header_echo(self):
        self.assertEqual(parse_boot_reply(reply(8, b"\x08\x60"), command=8, payload_size=2), b"\x08\x60")
        for bad in (reply(8)[1:], b"\0" + reply(8), reply(7), reply(8, status=1), reply(8)[:19]):
            with self.subTest(reply=bad), self.assertRaises(ProtocolError):
                parse_boot_reply(bad, command=8, payload_size=2)

    def test_boot_target_query_rejects_unknown_usb_device_before_io(self):
        transport = QueueTransport()
        with self.assertRaises(UnsupportedProtocol):
            query_boot_target(transport, usb_pid=0x3109)
        self.assertEqual(transport.sent, [])

    def test_boot_target_timeout_has_no_retry_or_mode_change(self):
        transport = QueueTransport()
        with self.assertRaises(ProtocolTimeout):
            query_boot_target(transport, usb_pid=0x3208)
        self.assertEqual(len(transport.sent), 1)

    def test_boot_entry_rechecks_the_runtime_identity(self):
        transport = QueueTransport([bytes.fromhex("02226700008023000131").ljust(64, b"\0")])
        request_receiver_boot(transport, identity=IDENTITY)
        self.assertEqual(transport.sent[-1], BOOT_ENTRY_REPORT)
        self.assertEqual(len(transport.sent), 2)
        transport = QueueTransport([bytes.fromhex("02226800008023000131").ljust(64, b"\0")])
        with self.assertRaises(FirmwareMismatch):
            request_receiver_boot(transport, identity=IDENTITY)
        self.assertEqual(len(transport.sent), 1)

    def test_reset_queries_target_without_initialize_or_commit(self):
        transport = QueueTransport([reply(8, b"\x08\x60"), reply(7)])
        self.assertTrue(reset_receiver_boot(transport, usb_pid=0x3208))
        self.assertEqual(transport.sent, [build_boot_command(8, length=2, total_length=2), build_boot_command(7)])
        self.assertEqual(transport.reports, [], "Reset must drain its ACK before the caller closes HID")
        wrong = QueueTransport([reply(8, b"\x13\x30")])
        with self.assertRaises(FirmwareMismatch):
            reset_receiver_boot(wrong, usb_pid=0x3208)
        self.assertEqual(len(wrong.sent), 1)

    def test_reset_reply_failures_never_retry_or_claim_success(self):
        for response, error in (
            (TimeoutError("no reply"), ProtocolTimeout),
            (reply(8), ProtocolError),
            (reply(7, status=1), ProtocolError),
            (OSError("The device disconnected."), OSError),
            (OSError(errno.EACCES, "permission denied"), OSError),
        ):
            with self.subTest(response=response):
                transport = QueueTransport([reply(8, b"\x08\x60"), response])
                with self.assertRaises(error):
                    reset_receiver_boot(transport, usb_pid=0x3208)
                self.assertEqual(transport.sent, [
                    build_boot_command(8, length=2, total_length=2), build_boot_command(7),
                ])

    def test_reset_disconnect_after_send_requires_application_verification(self):
        for code in (errno.EIO, errno.ENODEV, errno.ENXIO):
            with self.subTest(errno=code):
                transport = QueueTransport([reply(8, b"\x08\x60"), OSError(code, "disconnected")])
                self.assertFalse(reset_receiver_boot(transport, usb_pid=0x3208))
                self.assertEqual(transport.reports, [])
                self.assertEqual(transport.sent, [
                    build_boot_command(8, length=2, total_length=2), build_boot_command(7),
                ])

    def test_reset_disconnect_during_target_query_is_not_accepted(self):
        transport = QueueTransport([OSError(errno.EIO, "disconnected")])
        with self.assertRaises(OSError):
            reset_receiver_boot(transport, usb_pid=0x3208)
        self.assertEqual(len(transport.sent), 1)

    def test_reset_send_disconnect_is_not_accepted_or_retried(self):
        class SendFailure(QueueTransport):
            def send(self, report):
                super().send(report)
                if report == build_boot_command(7):
                    raise OSError(errno.EIO, "send failed")

        transport = SendFailure([reply(8, b"\x08\x60"), reply(7)])
        with self.assertRaises(OSError):
            reset_receiver_boot(transport, usb_pid=0x3208)
        self.assertEqual(transport.sent.count(build_boot_command(7)), 1)
        self.assertEqual(transport.reports, [reply(7)])

    def test_plan_preserves_prefix_and_uses_relative_offsets(self):
        image = firmware()
        plan = plan_receiver_flash(image, INITIALIZATION)
        self.assertEqual(plan.image_offset, 0x4000)
        self.assertEqual(plan.data, b"".join(bytes([value]) * 4096 for value in (17, 34, 51, 68)))
        self.assertEqual(plan.page_count, 4)
        # Native uses initialization's prefix, not blindly the DAT destination.
        plan = plan_receiver_flash(image, replace(INITIALIZATION, image_offset=0x1000))
        self.assertEqual(plan.data, image.payload[0x1000:])

    def test_bad_geometry_and_mutated_image_are_rejected(self):
        image = firmware()
        for changed in (
            replace(INITIALIZATION, erase_size=0),
            replace(INITIALIZATION, erase_size=8192),
            replace(INITIALIZATION, image_offset=0),
            replace(INITIALIZATION, image_offset=1),
            replace(INITIALIZATION, image_offset=len(image.payload)),
        ):
            with self.subTest(layout=changed), self.assertRaises(UnsupportedProtocol):
                plan_receiver_flash(image, changed)
        with self.assertRaises(FirmwareMismatch):
            plan_receiver_flash(replace(image, payload=image.payload[:-1] + b"x"), INITIALIZATION)

    def flash(self, device, image, **fields):
        return flash_boot_receiver(
            device, image, identity=IDENTITY, boot_usb_pid=0x3208,
            qualification=qualification(image), expected_initialization=INITIALIZATION, **fields,
        )

    def test_complete_vendor_order_and_data_with_deferred_header_verification(self):
        image = firmware()
        device = ReceiverDevice(image)
        result = self.flash(device, image)
        self.assertEqual(device.commands, [8, 0xC1] + [4] * 4 + [3] * 512 + [0xC3, 0xC3, 0xC2, 0xC3, 0xC3, 7])
        requests = [packet for packet in device.sent if struct.unpack_from("<H", packet, 2)[0] == 0xC3]
        self.assertEqual([struct.unpack_from("<III", packet, 18) for packet in requests],
                         [(8192, 4096, 0), (12288, 4096, 0), (0, 4096, 0), (4096, 4096, 0)])
        self.assertEqual(device.received_commands[-1], 7, "Final reset must drain its ACK")
        self.assertEqual(result.bytes_verified, 16384)
        self.assertTrue(result.needs_reconnect_verification)

    def test_flash_reset_reply_failures_never_retry_or_return_verified_result(self):
        image = firmware()
        for response, error in (
            (TimeoutError("no reply"), ProtocolTimeout),
            (reply(8), ProtocolError),
            (reply(7, status=1), ProtocolError),
            (OSError("The device disconnected."), OSError),
            (OSError(errno.EACCES, "permission denied"), OSError),
        ):
            with self.subTest(response=response):
                class ResetFailureDevice(ReceiverDevice):
                    def receive(self, timeout):
                        if self.commands[-1] == 7:
                            if isinstance(response, Exception):
                                raise response
                            return response
                        return super().receive(timeout)

                device = ResetFailureDevice(image)
                with self.assertRaises(error):
                    self.flash(device, image)
                self.assertEqual(device.commands.count(7), 1)
                self.assertEqual(device.commands[-1], 7)
                self.assertEqual(device.commands.count(0xC2), 1)

    def test_flash_reset_disconnect_still_requires_reconnect_verification(self):
        image = firmware()
        for code in (errno.EIO, errno.ENODEV, errno.ENXIO):
            with self.subTest(errno=code):
                class DisconnectedResetDevice(ReceiverDevice):
                    def receive(self, timeout):
                        if self.commands[-1] == 7:
                            raise OSError(code, "disconnected")
                        return super().receive(timeout)

                device = DisconnectedResetDevice(image)
                result = self.flash(device, image)
                self.assertTrue(result.needs_reconnect_verification)
                self.assertEqual(device.commands.count(7), 1)
                self.assertEqual(device.commands[-1], 7)

    def test_flash_reset_send_disconnect_is_not_accepted_or_retried(self):
        class SendFailureDevice(ReceiverDevice):
            def send(self, report):
                super().send(report)
                if self.commands[-1] == 7:
                    raise OSError(errno.EIO, "send failed")

        image = firmware()
        device = SendFailureDevice(image)
        with self.assertRaises(OSError):
            self.flash(device, image)
        self.assertEqual(device.commands.count(7), 1)
        self.assertNotEqual(device.received_commands[-1], 7)

    def experimental_authorization(self, image):
        prefix = image.payload[:INITIALIZATION.image_offset]
        return LegacySourceReviewedAuthorization(
            0x6008, None, 0, image.sha256, "synthetic source-review fixture only", (103,),
            INITIALIZATION, hashlib.sha256(prefix).hexdigest(), crc16(prefix),
        )

    def test_experimental_source_review_is_explicit_and_validates_before_device_io(self):
        image = firmware()
        authorization = self.experimental_authorization(image)
        self.assertEqual(authorization.evidence_level, "source-reviewed-experimental")
        self.assertEqual(validate_receiver_authorization(image, IDENTITY, authorization), INITIALIZATION)
        device = ReceiverDevice(image)
        flash_boot_receiver(device, image, identity=IDENTITY, boot_usb_pid=0x3208,
                            qualification=authorization)
        self.assertEqual(device.commands[-1], 7)

    def test_bad_experimental_bindings_never_send_even_a_boot_query(self):
        image = firmware()
        authorization = self.experimental_authorization(image)
        for changed in (
            replace(authorization, initialization=None),
            replace(authorization, initialization=replace(INITIALIZATION, image_offset=0x1000)),
            replace(authorization, boot_prefix_sha256="0" * 64),
            replace(authorization, boot_prefix_crc16=authorization.boot_prefix_crc16 ^ 1),
            replace(authorization, source_versions=(102,)),
            replace(authorization, image_sha256="0" * 64),
            replace(authorization, evidence=""),
            FlashAuthorization(0x6008, None, 0, image.sha256, "untyped", (103,)),
        ):
            device = QueueTransport()
            with self.subTest(authorization=changed), self.assertRaises((FirmwareMismatch, UnsupportedProtocol)):
                flash_boot_receiver(device, image, identity=IDENTITY, boot_usb_pid=0x3208,
                                    qualification=changed)
            self.assertEqual(device.sent, [])

    def test_caller_cannot_override_experimental_initialization(self):
        image = firmware()
        with self.assertRaises(FirmwareMismatch):
            validate_receiver_authorization(
                image, IDENTITY, self.experimental_authorization(image),
                expected_initialization=replace(INITIALIZATION, upgrade_length=0x40000),
            )

    def test_wrong_qualification_never_touches_device(self):
        image = firmware()
        for bad in (
            replace(qualification(image), product_id=0x6013),
            replace(qualification(image), image_sha256="0" * 64),
            replace(qualification(image), source_versions=(102,)),
            replace(qualification(image), evidence=""),
        ):
            device = QueueTransport()
            with self.subTest(qualification=bad), self.assertRaises(FirmwareMismatch):
                flash_boot_receiver(device, image, identity=IDENTITY, boot_usb_pid=0x3208,
                                    qualification=bad, expected_initialization=INITIALIZATION)
            self.assertEqual(device.sent, [])

    def test_unexpected_initialization_stops_before_erase(self):
        image = firmware()
        for field in INITIALIZATION.__dict__:
            changed = replace(INITIALIZATION, **{field: getattr(INITIALIZATION, field) ^ 1})
            device = ReceiverDevice(image, initialization=changed)
            with self.subTest(field=field), self.assertRaises(FirmwareMismatch):
                self.flash(device, image)
            self.assertEqual(device.commands, [8, 0xC1])

    def test_rejections_stop_without_retry_finalize_or_reset(self):
        image = firmware()
        for command in (0xC1, 4, 3, 0xC3, 0xC2):
            device = ReceiverDevice(image, fail=command)
            with self.subTest(command=command), self.assertRaises(ProtocolError):
                self.flash(device, image)
            self.assertEqual(device.commands[-1], command)
            self.assertNotIn(7, device.commands)
            if command != 0xC2:
                self.assertNotIn(0xC2, device.commands)

    def test_crc_failure_before_or_after_save_stops_without_reset(self):
        image = firmware()
        for page in (0, 1, 2, 3):
            device = ReceiverDevice(image, bad_crc_page=page)
            with self.subTest(page=page), self.assertRaisesRegex(ProtocolError, "CRC failed"):
                self.flash(device, image)
            self.assertNotIn(7, device.commands)
            self.assertEqual(0xC2 in device.commands, page < 2)

    def test_timeout_after_a_write_never_retries_or_commits(self):
        image = firmware()
        device = ReceiverDevice(image, timeout_command=3)
        with self.assertRaises(ProtocolTimeout):
            self.flash(device, image)
        self.assertEqual(device.commands, [8, 0xC1, 4, 4, 4, 4, 3])


if __name__ == "__main__":
    unittest.main()
