import hashlib
import struct
import unittest
from dataclasses import replace

from eightbitdo.protocol import (
    HEADER, FirmwareMismatch, ProtocolError, SourceRegion, SourceReviewedAuthorization,
    UnsupportedProtocol, crc16, parse_firmware,
)
from eightbitdo.recovery import finish_recovery, inspect_recovery


def application(version, size):
    data = bytearray((i % 251 for i in range(size)))
    struct.pack_into('<I', data, 2, version)
    data[8:12] = b'KNLT'
    struct.pack_into('<H', data, 12, 16)
    struct.pack_into('<I', data, 24, size - 32)
    struct.pack_into('<H', data, 28, 0x6013)
    data[30] = 1
    struct.pack_into('<I', data, size - 32, sum(data[:size - 36]))
    return bytes(data)


class BootDevice:
    def __init__(self):
        self.memory = bytearray(b'\xff' * 0x1F000)
        self.sent = []
        self.pid = 0x6013
        self.bad_header = None
        self.short_reply = False
        self.reset_failure = False
        self.boot = bytes(i % 251 for i in range(0x5000))
        self.source = application(101, 256)
        self.memory[:0x5000] = self.boot
        self.memory[0x5000:0x5100] = self.source
        payload = application(106, 512)
        self.image = parse_firmware(struct.pack('<7I', 106, 0x5000, len(payload), 0x6013, 0, 0, 0) + payload)
        self.authorization = SourceReviewedAuthorization(
            0x6013, None, 0, self.image.sha256, 'Synthetic recovery fixture only', (101,),
            (SourceRegion(0, len(self.boot), hashlib.sha256(self.boot).hexdigest()),
             SourceRegion(0x5000, len(self.source), hashlib.sha256(self.source).hexdigest())),
        )

    def send(self, report):
        assert len(report) == 65 and report[:2] == b'\x00\x05'
        command, parameters, length, checksum, total, offset = HEADER.unpack_from(report, 2)
        assert command in (5, 7, 8) and parameters == checksum == 0 and total == length
        assert report[18:] == bytes(47)
        self.sent.append(report)
        data = b''
        if command == 8:
            data = struct.pack('<H', self.pid)
        elif command == 5:
            data = bytes(self.memory[offset:offset + length])
        elif self.reset_failure:
            raise OSError('Disconnected during reset')
        header = [0, command, length, 0, total, offset]
        if self.bad_header is not None:
            header[self.bad_header] ^= 1
        self.reply = (b'\x00\x05' + HEADER.pack(*header) + data).ljust(64, b'\0')
        if self.short_reply:
            self.reply = self.reply[:-1]

    def receive(self, timeout):
        return self.reply

    @property
    def commands(self):
        return [HEADER.unpack_from(report, 2)[0] for report in self.sent]

    def pending(self, **overrides):
        record = bytearray(52)
        values = {'version': 106, 'marker': 0xAEB14D6C, 'staging': 0x13000, 'length': len(self.image.payload)}
        values.update(overrides)
        for key, offset in [('version', 12), ('marker', 24), ('staging', 28), ('length', 32)]:
            struct.pack_into('<I', record, offset, values[key])
        struct.pack_into('<I', record, 48, crc16(record[:48]))
        self.memory[0x12000:0x12034] = record
        self.memory[0x13000:0x13000 + len(self.image.payload)] = self.image.payload


class RecoveryTests(unittest.TestCase):
    def inspect(self, device):
        return inspect_recovery(device, device.image, authorization=device.authorization)

    def finish(self, device):
        return finish_recovery(device, device.image, authorization=device.authorization)

    def test_inspection_does_not_reset_valid_pending_image(self):
        device = BootDevice()
        device.pending()
        # An interrupted copy may already have destroyed the installed application.
        device.memory[0x5000:0x6000] = b'\xff' * 4096
        status = self.inspect(device)
        self.assertEqual(status.action, 'finish-pending')
        self.assertTrue(status.can_reset)
        self.assertEqual(status.evidence_level, 'source-reviewed-experimental')
        self.assertEqual(set(device.commands), {5, 8})
        self.assertEqual(device.commands[0], 8)

    def test_finish_rechecks_pending_bytes_before_single_reset(self):
        device = BootDevice()
        device.pending()
        self.inspect(device)
        device.sent.clear()
        result = self.finish(device)
        self.assertEqual(result.action, 'finish-pending')
        self.assertTrue(result.vendor_copy_expected)
        self.assertTrue(result.needs_reconnect_verification)
        self.assertEqual(device.commands.count(7), 1)
        self.assertEqual(device.commands[-1], 7)
        self.assertTrue(set(device.commands) <= {5, 7, 8})

    def test_source_or_target_application_can_return_without_pending_copy(self):
        for target in (False, True):
            for erased in (False, True):
                with self.subTest(target=target, erased=erased):
                    device = BootDevice()
                    if not erased:
                        record = bytearray(52)
                        struct.pack_into('<I', record, 48, crc16(record[:48]))
                        device.memory[0x12000:0x12034] = record
                    if target:
                        device.memory[0x5000:0x5200] = device.image.payload
                    result = self.finish(device)
                    self.assertEqual(result.action, 'return-installed')
                    self.assertFalse(result.vendor_copy_expected)
                    self.assertEqual(device.commands[-1], 7)

    def test_invalid_installed_image_is_readiness_only_without_pending_record(self):
        device = BootDevice()
        device.memory[0x5000] ^= 1
        status = self.inspect(device)
        self.assertEqual(status.action, 'blocked')
        self.assertFalse(status.can_reset)
        with self.assertRaises(UnsupportedProtocol):
            self.finish(device)
        self.assertNotIn(7, device.commands)

    def test_pending_target_fields_must_match_authorized_image(self):
        for override in ({'version': 107}, {'staging': 0x5000}, {'length': 4096}):
            with self.subTest(override=override):
                device = BootDevice()
                device.pending(**override)
                with self.assertRaisesRegex(FirmwareMismatch, 'Pending metadata'):
                    self.finish(device)
                self.assertNotIn(7, device.commands)

    def test_corrupt_metadata_does_not_reset(self):
        device = BootDevice()
        device.pending()
        device.memory[0x12030] ^= 1
        with self.assertRaisesRegex(ProtocolError, 'metadata is damaged'):
            self.finish(device)
        self.assertNotIn(7, device.commands)

    def test_staging_corruption_or_unreviewed_sector_padding_does_not_reset(self):
        for offset in (0x13000, 0x131FF, 0x13FFF):
            with self.subTest(offset=offset):
                device = BootDevice()
                device.pending()
                self.inspect(device)
                device.memory[offset] ^= 1
                with self.assertRaisesRegex(FirmwareMismatch, 'staging bytes or sector padding'):
                    self.finish(device)
                self.assertNotIn(7, device.commands)

    def test_boot_identity_and_fingerprint_are_required_before_metadata_reads(self):
        for wrong_pid in (False, True):
            with self.subTest(wrong_pid=wrong_pid):
                device = BootDevice()
                if wrong_pid:
                    device.pid = 0x6012
                else:
                    device.memory[0x4FFF] ^= 1
                with self.assertRaises(FirmwareMismatch):
                    self.finish(device)
                self.assertNotIn(7, device.commands)
                addresses = [HEADER.unpack_from(report, 2)[5] for report in device.sent]
                self.assertNotIn(0x12000, addresses)

    def test_unknown_policy_fails_without_any_io(self):
        for changes in ({'product_id': 0x6012}, {'source_regions': ()}, {'source_versions': ()},
                        {'image_sha256': '0' * 64}):
            with self.subTest(changes=changes):
                device = BootDevice()
                device.authorization = replace(device.authorization, **changes)
                with self.assertRaises(ProtocolError):
                    self.finish(device)
                self.assertEqual(device.sent, [])

    def test_bad_boot_reply_fails_without_reset(self):
        for field in range(6):
            with self.subTest(field=field):
                device = BootDevice()
                device.bad_header = field
                with self.assertRaises(ProtocolError):
                    self.finish(device)
                self.assertEqual(device.commands, [8])
        device = BootDevice()
        device.short_reply = True
        with self.assertRaises(ProtocolError):
            self.finish(device)
        self.assertEqual(device.commands, [8])

    def test_reset_transport_failure_is_never_retried(self):
        device = BootDevice()
        device.reset_failure = True
        with self.assertRaises(OSError):
            self.finish(device)
        self.assertEqual(device.commands.count(7), 1)

    def test_authorized_image_with_invalid_boot_checksum_fails_without_io(self):
        device = BootDevice()
        data = bytearray(struct.pack('<7I', 106, 0x5000, 512, 0x6013, 0, 0, 0) + device.image.payload)
        data[28 + 128] ^= 1
        device.image = parse_firmware(bytes(data))
        device.authorization = replace(device.authorization, image_sha256=device.image.sha256)
        with self.assertRaisesRegex(FirmwareMismatch, 'bootloader checks'):
            self.finish(device)
        self.assertEqual(device.sent, [])


if __name__ == '__main__':
    unittest.main()
