import hashlib
import struct
import unittest
from dataclasses import replace

from eightbitdo.protocol import (
    DeviceIdentity, FlashQualification, FirmwareMismatch, ProtocolError,
    ProtocolTimeout, UnsupportedProtocol, build_command, crc16, flash_application,
    parse_command_reply, parse_firmware, parse_identity_reply, query_identity, query_paired_identity,
)


def identity_report(pid=0x6013, version=106):
    return (b"\x02\x22" + struct.pack("<I", version)).ljust(64, b"\0")


def product_reply(pid=0x6013):
    return (b"\x02\x05" + struct.pack("<HHHHIIH", 0, 8, 2, 0, 2, 0, pid)).ljust(64, b"\0")


def firmware(pid=0x6013, size=96):
    payload = bytearray((i % 251 for i in range(size)))
    payload[8:12] = b"KNLT"
    return struct.pack("<7I", 107, 0x5000, size, pid, 0, 0, 0) + payload


class QueueTransport:
    def __init__(self, reports):
        self.reports = list(reports)
        self.sent = []

    def send(self, report):
        self.sent.append(report)

    def receive(self, timeout):
        if not self.reports:
            raise TimeoutError()
        return self.reports.pop(0)


class FlashDevice:
    """An application-mode fake; successful tests are not hardware qualification."""

    def __init__(self, size=96, fail_command=None, bad_readback=False, wrong_offset=False, bad_crc=False,
                 pid=0x6013, bad_readback_offset=0):
        self.pid = pid
        self.staging_base = {0x6012: 0x32000, 0x6013: 0x13000}[pid]
        self.memory = bytearray(self.staging_base + size)
        self.sent = []
        self.reply = None
        self.fail_command = fail_command
        self.bad_readback = bad_readback
        self.bad_readback_offset = bad_readback_offset
        self.wrong_offset = wrong_offset
        self.bad_crc = bad_crc

    def send(self, report):
        self.sent.append(report)
        command, params, length, checksum, total, offset = struct.unpack_from("<HHHHII", report, 2)
        payload = report[18:18 + length]
        if command == 0x2100:
            self.reply = identity_report()
            return
        status = 1 if command == self.fail_command else 0
        data = b""
        if command == 8:
            data = struct.pack("<H", self.pid)
        if command == 4:
            start = self.staging_base + offset
            self.memory[start:start + total] = b"\xff" * total
        if command == 3:
            assert crc16(payload) == checksum
            start = self.staging_base + offset
            self.memory[start:start + length] = payload
        if command == 0xC3:
            start, count, block = struct.unpack("<III", payload)
            assert block == 4096
            start += self.staging_base
            data = struct.pack("<H", crc16(self.memory[start:start + count]) ^ int(self.bad_crc))
        if command == 5:
            data = bytes(self.memory[offset:offset + length])
            if self.bad_readback and offset == self.staging_base + self.bad_readback_offset:
                data = bytes([data[0] ^ 1]) + data[1:]
        response_offset = offset + 1 if self.wrong_offset and command == 3 else offset
        self.reply = (
            b"\x02\x05" + struct.pack("<HHHHII", status, command, len(data), 0, total, response_offset) + data
        ).ljust(64, b"\0")

    def receive(self, timeout):
        return self.reply

    @property
    def commands(self):
        return [struct.unpack_from("<H", data, 2)[0] for data in self.sent]


def qualification(image):
    return FlashQualification(image.pid, None, image.revision, image.sha256, "Test fixture only", (106,))


class ProtocolTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(crc16(b"123456789"), 0x4B37)
        self.assertEqual(crc16(b""), 0xFFFF)

    def test_command_boundaries(self):
        report = build_command(3, data=bytes(range(46)), total_length=80, offset=16)
        self.assertEqual(len(report), 64)
        self.assertEqual(report[:2], b"\x81\x05")
        self.assertEqual(struct.unpack_from("<HHHHII", report, 2), (3, 0, 46, crc16(bytes(range(46))), 80, 16))
        for kwargs in [{"data": b"x" * 47}, {"offset": -1}, {"length": 47}, {"data": b"xx", "length": 1}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ProtocolError):
                build_command(3, **kwargs)

    def test_identity_query_ignores_gamepad_reports(self):
        transport = QueueTransport([product_reply(), b"\x01\x02\x03", identity_report()])
        identity = query_identity(transport, usb_pid=0x6013)
        self.assertEqual(identity, DeviceIdentity(0x6013, 106))
        self.assertEqual(identity.version, "1.06")
        self.assertEqual(transport.sent[1], bytes.fromhex("81 05 00 21 01").ljust(64, b"\0"))
        self.assertIsNone(identity.hardware_revision)

    def test_legacy_identity_uses_model_tag_and_single_byte_beta(self):
        report = bytes.fromhex("02 22 67 00 00 80 23 00 01 31")
        result = parse_identity_reply(report, usb_pid=0x3109)
        self.assertEqual(result, DeviceIdentity(0x6008, 103, beta=0))
        # Byte 5 is a flag, and bytes 8 onward are not a firmware PID.
        changed = report[:4] + bytes.fromhex("03 ff") + report[6:8] + b"\xff" * 56
        self.assertEqual(parse_identity_reply(changed, usb_pid=0x3109), DeviceIdentity(0x6008, 103, beta=3))

    def test_legacy_query_distinguishes_known_models_without_modern_commands(self):
        cases = [(0x3109, 0x0023, 0x6008), (0x3109, 0x3013, 0x3013),
                 (0x3109, 0x9023, 0x3013), (0x6007, 0x0022, 0x6007),
                 (0x6007, 0x0023, 0x6008)]
        for usb_pid, tag, target in cases:
            with self.subTest(usb_pid=usb_pid, tag=tag):
                report = b"\x02\x22" + struct.pack("<HHH", 103, 0x8000, tag)
                transport = QueueTransport([report])
                self.assertEqual(query_identity(transport, usb_pid=usb_pid), DeviceIdentity(target, 103))
                self.assertEqual(transport.sent, [bytes.fromhex("81 05 00 21 01").ljust(64, b"\0")])

    def test_legacy_unknown_tags_do_not_inherit_vendor_default(self):
        for usb_pid, tag in [(0x3109, 0), (0x3109, 0x6008), (0x3109, 0x0022),
                             (0x6007, 0), (0x6007, 0x0123), (0x6007, 0x3013)]:
            with self.subTest(usb_pid=usb_pid, tag=tag):
                report = b"\x02\x22" + struct.pack("<HHH", 103, 0x8000, tag)
                result = parse_identity_reply(report, usb_pid=usb_pid)
                self.assertEqual(result, DeviceIdentity(usb_pid, 103, target_verified=False))

    def test_legacy_truncated_tag_is_rejected(self):
        for usb_pid in (0x3109, 0x6007):
            for length in (6, 7):
                with self.subTest(usb_pid=usb_pid, length=length), self.assertRaises(ProtocolError):
                    parse_identity_reply(bytes.fromhex("02 22 67 00 00 80 23 00")[:length], usb_pid=usb_pid)

    def test_identity_rejects_mismatch_and_short_reply(self):
        with self.assertRaises(FirmwareMismatch):
            parse_identity_reply(identity_report(), usb_pid=0x6013, verified_pid=0x6012)
        with self.assertRaises(ProtocolError):
            parse_identity_reply(b"\x02\x22\x00", usb_pid=0x6013)

    def test_unknown_pid_sends_nothing(self):
        transport = QueueTransport([])
        with self.assertRaises(UnsupportedProtocol):
            query_identity(transport, usb_pid=0x3208)
        self.assertEqual(transport.sent, [])

    def test_timeout_is_not_retried(self):
        transport = QueueTransport([])
        with self.assertRaises(ProtocolTimeout):
            query_identity(transport, usb_pid=0x6013)
        self.assertEqual(len(transport.sent), 1)

    def test_bad_timeout_sends_nothing(self):
        transport = QueueTransport([])
        with self.assertRaises(ProtocolError):
            query_identity(transport, usb_pid=0x6013, timeout=0)
        self.assertEqual(transport.sent, [])

    def test_paired_controller_version_uses_receiver_query(self):
        reply = (
            b"\x02\x04" + struct.pack("<HHHHII", 4, 0x121, 6, 0, 0, 0)
            + bytes([75]) + struct.pack("<H", 110) + bytes([0, 0, 2])
        ).ljust(64, b"\0")
        transport = QueueTransport([reply])
        identity = query_paired_identity(transport)
        self.assertEqual(identity.version, "1.10")
        self.assertEqual(identity.battery_percent, 75)
        self.assertEqual(identity.edition, 2)
        self.assertEqual(transport.sent[0][:8], bytes.fromhex("81 04 21 01 00 00 06 00"))

    def test_paired_query_rejects_other_receivers_without_io(self):
        transport = QueueTransport([])
        with self.assertRaises(UnsupportedProtocol):
            query_paired_identity(transport, receiver_pid=0x3109)
        self.assertEqual(transport.sent, [])

    def test_paired_query_timeout_does_not_retry(self):
        transport = QueueTransport([])
        with self.assertRaises(ProtocolTimeout):
            query_paired_identity(transport)
        self.assertEqual(len(transport.sent), 1)

    def test_paired_query_rejects_wrong_command(self):
        reply = (b"\x02\x04" + struct.pack("<HHHHII", 4, 2, 6, 0, 0, 0) + b"\0" * 6).ljust(64, b"\0")
        with self.assertRaises(ProtocolError):
            query_paired_identity(QueueTransport([reply]))

    def test_firmware_exact_target_and_hash(self):
        data = firmware()
        image = parse_firmware(data, expected_pid=0x6013, expected_revision=0, expected_version=107)
        self.assertEqual(image.version, "1.07")
        self.assertEqual(image.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(image.payload, data[28:])

    def test_firmware_rejects_target_revision_version_and_length(self):
        for kwargs in [{"expected_pid": 0x6012}, {"expected_revision": 1}, {"expected_version": 106}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(FirmwareMismatch):
                parse_firmware(firmware(), **kwargs)
        for data in [b"", firmware()[:-1], firmware() + b"\0", b"\0" * 28]:
            with self.subTest(length=len(data)), self.assertRaises(ProtocolError):
                parse_firmware(data)

    def test_reply_parser_rejects_truncation_and_boot_framing(self):
        for report in [b"\x02\x05", b"\x02\x40\x05" + b"\0" * 61, b"\x02\x05" + struct.pack("<HHHHII", 0, 5, 47, 0, 0, 0)]:
            with self.assertRaises(ProtocolError):
                parse_command_reply(report)

    def test_flash_transaction_verifies_before_commit(self):
        for pid, staging_base in [(0x6012, 0x32000), (0x6013, 0x13000)]:
            with self.subTest(pid=pid):
                image = parse_firmware(firmware(pid=pid, size=4150))
                transport = FlashDevice(pid=pid, size=4150)
                progress = []
                result = flash_application(
                    transport, image, identity=DeviceIdentity(pid, 106), qualification=qualification(image),
                    progress=lambda *values: progress.append(values),
                )
                self.assertEqual(transport.memory[staging_base:], image.payload)
                self.assertEqual(transport.memory[:staging_base], bytes(staging_base))
                writes = [struct.unpack_from("<I", report, 14)[0]
                          for report in transport.sent if report[2:4] == b"\x03\0"]
                reads = [struct.unpack_from("<I", report, 14)[0]
                         for report in transport.sent if report[2:4] == b"\x05\0"]
                logical_offsets = list(range(0, 4096, 46)) + list(range(4096, 4150, 46))
                self.assertEqual(writes, logical_offsets)
                self.assertEqual(reads, [staging_base + offset for offset in logical_offsets])
                self.assertEqual(transport.commands.count(4), 2)
                self.assertEqual(transport.commands.count(0xC3), 2)
                self.assertEqual(transport.commands[-2:], [0xC4, 7])
                self.assertTrue(result.needs_reconnect_verification)
                self.assertEqual(result.bytes_verified, 4150)
                self.assertEqual(progress[-1], ("restarting", 4150, 4150))

    def test_flash_requires_exact_qualification_before_any_io(self):
        image = parse_firmware(firmware())
        transport = FlashDevice()
        bad = FlashQualification(0x6013, None, 0, "0" * 64, "test")
        with self.assertRaises(FirmwareMismatch):
            flash_application(transport, image, identity=DeviceIdentity(0x6013, 106), qualification=bad)
        self.assertEqual(transport.sent, [])

    def test_changed_device_stops_before_erase(self):
        image = parse_firmware(firmware())
        transport = QueueTransport([product_reply(), identity_report(version=108)])
        with self.assertRaises(FirmwareMismatch):
            flash_application(transport, image, identity=DeviceIdentity(0x6013, 106), qualification=qualification(image))
        self.assertEqual(len(transport.sent), 2)

    def test_write_failure_never_retries_commits_or_resets(self):
        image = parse_firmware(firmware())
        transport = FlashDevice(fail_command=3)
        with self.assertRaises(ProtocolError):
            flash_application(transport, image, identity=DeviceIdentity(0x6013, 106), qualification=qualification(image))
        self.assertEqual(transport.commands, [8, 0x2100, 4, 3])

    def test_readback_failure_never_commits(self):
        for pid in (0x6012, 0x6013):
            with self.subTest(pid=pid):
                image = parse_firmware(firmware(pid=pid, size=4150))
                transport = FlashDevice(pid=pid, size=4150, bad_readback=True, bad_readback_offset=4096)
                with self.assertRaisesRegex(ProtocolError, "readback verification"):
                    flash_application(transport, image, identity=DeviceIdentity(pid, 106),
                                      qualification=qualification(image))
                self.assertEqual(transport.commands.count(0xC3), 2)
                self.assertNotIn(0xC4, transport.commands)
                self.assertNotIn(7, transport.commands)

    def test_crc_failure_never_commits(self):
        image = parse_firmware(firmware())
        transport = FlashDevice(bad_crc=True)
        with self.assertRaisesRegex(ProtocolError, "CRC verification"):
            flash_application(transport, image, identity=DeviceIdentity(0x6013, 106), qualification=qualification(image))
        self.assertNotIn(0xC4, transport.commands)
        self.assertNotIn(7, transport.commands)

    def test_qualified_image_payload_cannot_change(self):
        image = parse_firmware(firmware())
        mutated = replace(image, payload=image.payload[:-1] + bytes([image.payload[-1] ^ 1]))
        transport = FlashDevice()
        with self.assertRaisesRegex(FirmwareMismatch, "changed after qualification"):
            flash_application(transport, mutated, identity=DeviceIdentity(0x6013, 106), qualification=qualification(image))
        self.assertEqual(transport.sent, [])

    def test_bootloader_framing_stops_before_erase(self):
        image = parse_firmware(firmware())
        boot_reply = b"\x02\x40\x05" + b"\0" * 61
        transport = QueueTransport([boot_reply])
        with self.assertRaises(ProtocolTimeout):
            flash_application(transport, image, identity=DeviceIdentity(0x6013, 106), qualification=qualification(image))
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(struct.unpack_from("<H", transport.sent[-1], 2)[0], 8)

    def test_wrong_command_ack_stops_before_erase(self):
        image = parse_firmware(firmware())
        stale_reply = b"\x02\x05" + struct.pack("<HHHHII", 0, 3, 0, 0, 0, 0)
        transport = QueueTransport([stale_reply])
        with self.assertRaisesRegex(ProtocolError, "different command"):
            flash_application(transport, image, identity=DeviceIdentity(0x6013, 106), qualification=qualification(image))
        self.assertEqual(len(transport.sent), 1)

    def test_stale_write_ack_stops_transaction(self):
        image = parse_firmware(firmware())
        transport = FlashDevice(wrong_offset=True)
        with self.assertRaises(ProtocolError):
            flash_application(transport, image, identity=DeviceIdentity(0x6013, 106), qualification=qualification(image))
        self.assertEqual(transport.commands, [8, 0x2100, 4, 3])


if __name__ == "__main__":
    unittest.main()
