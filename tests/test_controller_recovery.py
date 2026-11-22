import hashlib
import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

from eightbitdo import controller_recovery as recovery
from eightbitdo.protocol import (
    HEADER, FirmwareMismatch, ProtocolError, SourceRegion, SourceReviewedAuthorization,
    UnsupportedProtocol, crc16, parse_firmware,
)


def application(version, size=512):
    data = bytearray((i % 251 for i in range(size)))
    struct.pack_into('<I', data, 2, version)
    data[8:12] = b'KNLT'
    struct.pack_into('<I', data, 24, 128)
    struct.pack_into('<H', data, 28, 0x6012)
    data[30] = 1
    struct.pack_into('<I', data, 128, sum(data[:124]))
    # Boot0x1988: second component at primary_length+4. Boot0x1904:
    # its extent is stored at primary_length+24 (secondary header+20).
    struct.pack_into('<I', data, 152, size - 136)
    struct.pack_into('<I', data, size - 4, sum(data[132:size - 4]))
    return bytes(data)


class ControllerBoot:
    def __init__(self):
        self.memory = bytearray(b'\xff' * 0x5E000)
        self.sent = []
        self.pid = 0x6012
        self.bad_header = None
        self.short_reply = False
        self.reset_failure = False
        self.timeout = False
        self.fail_command = None
        self.fail_occurrence = 1
        self.bad_crc = False
        self.corrupt_readback = False
        self.change_metadata = False
        self.bad_mutation_header = None
        self.boot = bytes(i % 251 for i in range(0x5000))
        self.source = application(109)
        self.memory[:0x5000] = self.boot
        self.memory[0x5000:0x5200] = self.source
        payload = application(110, 768)
        self.image = parse_firmware(struct.pack('<7I', 110, 0x5000, len(payload), 0x6012, 0, 0, 0) + payload)
        self.authorization = SourceReviewedAuthorization(
            0x6012, None, 0, self.image.sha256, 'Synthetic controller fixture only', (109,),
            (SourceRegion(0, len(self.boot), hashlib.sha256(self.boot).hexdigest()),
             SourceRegion(0x5000, len(self.source), hashlib.sha256(self.source).hexdigest())),
        )

    def send(self, report):
        assert len(report) == 65 and report[:2] == b'\x00\x05'
        command, parameters, length, checksum, total, offset = HEADER.unpack_from(report, 2)
        assert parameters == 0
        self.sent.append(report)
        if command == self.fail_command and self.commands.count(command) == self.fail_occurrence:
            raise OSError('Injected transport failure')
        data = b''
        if command == 8:
            assert checksum == 0 and length == total == 2 and offset == 0
            data = struct.pack('<H', self.pid)
        elif command == 5:
            assert checksum == 0 and total == length
            assert 0 < length <= 32 and offset + length <= 0x5E000
            data = bytes(self.memory[offset:offset + length])
            if self.corrupt_readback and 3 in self.commands and offset == 0x5000:
                data = bytes([data[0] ^ 1]) + data[1:]
        elif command == 4:
            assert length == checksum == 0 and total == 4096
            assert offset % 4096 == 0 and 0x5000 <= offset < 0x31000
            self.memory[offset:offset + total] = b'\xff' * total
            total = 0
        elif command == 3:
            assert length == total and 1 <= length <= 46
            assert 0x5000 <= offset and offset + length <= 0x31000
            chunk = report[18:18 + length]
            assert crc16(chunk) == checksum
            self.memory[offset:offset + length] = chunk
            if self.change_metadata:
                self.memory[0x31001] = 0
        elif command == 0xC3:
            assert length == total == 12 and offset == 0
            query = report[18:30]
            assert crc16(query) == checksum
            address, count, block_size = struct.unpack('<III', query)
            assert 0x5000 <= address and address + count <= 0x31000 and 1 <= count <= 256
            assert block_size == 4096
            checksum_result = crc16(self.memory[address:address + count])
            data = struct.pack('<H', checksum_result ^ self.bad_crc)
        elif self.reset_failure:
            raise OSError('Disconnected during reset')
        else:
            assert command == 7 and checksum == length == total == offset == 0
        header = [0, command, length, checksum, total, offset]
        if self.bad_header is not None:
            header[self.bad_header] ^= 1
        if self.bad_mutation_header is not None and command in (3, 4, 0xC3):
            header[self.bad_mutation_header] ^= 1
        self.reply = (b'\x00\x05' + HEADER.pack(*header) + data).ljust(64, b'\0')
        if self.short_reply:
            self.reply = self.reply[:-1]

    def receive(self, timeout):
        assert timeout == (10.0 if self.commands[-1] == 4 else 2.0)
        if self.timeout:
            raise TimeoutError('No boot response')
        return self.reply

    @property
    def commands(self):
        return [HEADER.unpack_from(report, 2)[0] for report in self.sent]

    def pending(self, **overrides):
        record = bytearray(52)
        values = {'version': 110, 'marker': 0xAEB14D6C, 'staging': 0x32000, 'length': len(self.image.payload)}
        values.update(overrides)
        for key, offset in [('version', 12), ('marker', 24), ('staging', 28), ('length', 32)]:
            struct.pack_into('<I', record, offset, values[key])
        struct.pack_into('<I', record, 48, crc16(record[:48]))
        self.memory[0x31000:0x31034] = record
        self.memory[0x32000:0x32000 + len(self.image.payload)] = self.image.payload


class ControllerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.device = ControllerBoot()
        self.patch = patch.object(recovery, 'REVIEWED_BOOT_SHA256', self.device.authorization.source_regions[0].sha256)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def inspect(self):
        return recovery.inspect_controller_recovery(self.device, self.device.image, authorization=self.device.authorization)

    def finish(self):
        return recovery.finish_controller_recovery(self.device, self.device.image, authorization=self.device.authorization)

    def test_inspect_source_and_target_are_read_only(self):
        self.assertEqual(self.inspect().action, 'return-installed')
        self.assertEqual(set(self.device.commands), {5, 8})
        self.device.memory[0x5000:0x5300] = self.device.image.payload
        self.assertEqual(self.inspect().action, 'return-installed')
        self.assertEqual(set(self.device.commands), {5, 8})

    def test_pending_requires_complete_bytes_and_erased_sector_tail(self):
        self.device.pending()
        self.device.memory[0x5000:0x6000] = bytes(4096)
        self.assertEqual(self.inspect().action, 'finish-pending')
        self.assertNotIn(7, self.device.commands)
        self.device.memory[0x32FFF] = 0
        with self.assertRaises(FirmwareMismatch):
            self.finish()
        self.assertNotIn(7, self.device.commands)

    def test_finish_rechecks_and_resets_once(self):
        self.inspect()
        self.device.sent.clear()
        result = self.finish()
        self.assertTrue(result.needs_reconnect_verification)
        self.assertFalse(result.vendor_copy_expected)
        self.assertEqual(self.device.commands.count(7), 1)
        self.assertEqual(self.device.commands[0], 8)
        self.assertEqual(self.device.commands[-1], 7)

    def test_pending_reset_reports_vendor_flash_copy(self):
        self.device.pending()
        self.assertTrue(self.finish().vendor_copy_expected)

    def test_secondary_corruption_blocks_return(self):
        self.device.memory[0x5000 + 200] ^= 1
        self.assertEqual(self.inspect().action, 'blocked')
        with self.assertRaises(UnsupportedProtocol):
            self.finish()
        self.assertNotIn(7, self.device.commands)

    def test_reject_bad_image_checksums_without_io(self):
        for offset in (80, 200, 152):
            with self.subTest(offset=offset):
                payload = bytearray(self.device.image.payload)
                payload[offset] ^= 1
                image = parse_firmware(struct.pack('<7I', 110, 0x5000, len(payload), 0x6012, 0, 0, 0) + payload)
                authorization = replace(self.device.authorization, image_sha256=image.sha256)
                with self.assertRaises(FirmwareMismatch):
                    recovery.finish_controller_recovery(self.device, image, authorization=authorization)
                self.assertEqual(self.device.sent, [])

    def test_bad_policy_fails_before_io(self):
        original = self.device.authorization
        for fields in ({'product_id': 0x6013}, {'source_versions': ()}, {'source_regions': ()}, {'image_sha256': '0' * 64}):
            self.device.authorization = replace(original, **fields)
            with self.assertRaises(ProtocolError):
                self.finish()
            self.assertEqual(self.device.sent, [])

    def test_identity_and_boot_fingerprint_stop_before_metadata(self):
        self.device.pid = 0x6013
        with self.assertRaises(FirmwareMismatch):
            self.finish()
        self.assertEqual(self.device.commands, [8])
        self.device.pid = 0x6012
        self.device.memory[0x4FFF] ^= 1
        with self.assertRaises(FirmwareMismatch):
            self.finish()
        self.assertNotIn(0x31000, [HEADER.unpack_from(r, 2)[5] for r in self.device.sent])

    def test_metadata_and_pending_mismatches_never_reset(self):
        for overrides in ({'version': 111}, {'staging': 0x5000}, {'length': 4096}):
            self.device.pending(**overrides)
            with self.assertRaises(FirmwareMismatch):
                self.finish()
        self.device.pending()
        self.device.memory[0x31030] ^= 1
        with self.assertRaises(ProtocolError):
            self.finish()
        self.assertNotIn(7, self.device.commands)

    def test_malformed_ack_and_timeout_stop_without_retry(self):
        for field in range(6):
            self.device.sent.clear()
            self.device.bad_header = field
            with self.assertRaises(ProtocolError):
                self.finish()
            self.assertEqual(self.device.commands, [8])
        self.device.bad_header = None
        self.device.short_reply = True
        with self.assertRaises(ProtocolError):
            self.finish()
        self.device.short_reply = False
        self.device.timeout = True
        self.device.sent.clear()
        with self.assertRaises(TimeoutError):
            self.finish()
        self.assertEqual(self.device.commands, [8])

    def test_reset_error_is_not_retried(self):
        self.device.reset_failure = True
        with self.assertRaises(OSError):
            self.finish()
        self.assertEqual(self.device.commands.count(7), 1)

    def test_read_and_command_bounds(self):
        for address, length in ((-1, 1), (0, 0), (0x5DFFF, 2)):
            with self.assertRaises(UnsupportedProtocol):
                recovery._read(self.device, address, length)
        for command in (3, 4, 0xC1, 0xC3, 0xC4):
            with self.assertRaises(UnsupportedProtocol):
                recovery._request(command)
        self.assertEqual(self.device.sent, [])

    def rescue(self, confirmed=True):
        return recovery.flash_controller_recovery(
            self.device, self.device.image, authorization=self.device.authorization, confirmed=confirmed,
        )

    def large_image(self):
        payload = application(110, 4864)
        self.device.image = parse_firmware(struct.pack('<7I', 110, 0x5000, len(payload), 0x6012, 0, 0, 0) + payload)
        self.device.authorization = replace(self.device.authorization, image_sha256=self.device.image.sha256)

    def test_full_rescue_verifies_and_preserves_protected_regions(self):
        self.large_image()
        before_boot = bytes(self.device.memory[:0x5000])
        before_protected = bytes(self.device.memory[0x31000:])
        result = self.rescue()
        self.assertEqual(result.bytes_verified, 4864)
        self.assertTrue(result.needs_reconnect_verification)
        self.assertEqual(bytes(self.device.memory[0x5000:0x5000 + 4864]), self.device.image.payload)
        self.assertEqual(bytes(self.device.memory[0x5000 + 4864:0x7000]), b'\xff' * (8192 - 4864))
        self.assertEqual(bytes(self.device.memory[:0x5000]), before_boot)
        self.assertEqual(bytes(self.device.memory[0x31000:]), before_protected)
        writes = [HEADER.unpack_from(r, 2)[5] for r in self.device.sent if HEADER.unpack_from(r, 2)[0] == 3]
        self.assertEqual(writes[0], 0x6000)
        self.assertLess(writes[-1], 0x6000)
        self.assertEqual(self.device.commands.count(4), 2)
        self.assertEqual(self.device.commands.count(7), 1)
        self.assertTrue(set(self.device.commands) <= {3, 4, 5, 7, 8, 0xC3})

    def test_unconfirmed_rescue_has_no_io(self):
        for confirmation in (False, 1, 'yes', None):
            with self.assertRaises(UnsupportedProtocol):
                self.rescue(confirmation)
        self.assertEqual(self.device.sent, [])

    def test_pending_rescue_cannot_erase_or_supersede_commit(self):
        self.device.pending()
        with self.assertRaises(UnsupportedProtocol):
            self.rescue()
        self.assertTrue(set(self.device.commands) <= {5, 8})

    def test_rescue_inspection_handles_corrupt_app_and_metadata_without_writes(self):
        self.device.memory[0x5000:0x6000] = b'\xff' * 4096
        for damaged in (False, True):
            if damaged:
                self.device.memory[0x31000] = 0
            status = recovery.inspect_controller_rescue(
                self.device, self.device.image, authorization=self.device.authorization,
            )
            self.assertEqual(status.action, 'reflash')
            self.assertFalse(status.can_reset)
            self.assertEqual(set(self.device.commands), {5, 8})
        self.device.pending()
        status = recovery.inspect_controller_rescue(
            self.device, self.device.image, authorization=self.device.authorization,
        )
        self.assertEqual(status.action, 'blocked')
        self.assertEqual(set(self.device.commands), {5, 8})

    def test_damaged_metadata_is_preserved_and_can_only_rescue_valid_app(self):
        self.device.pending()
        self.device.memory[0x31030] ^= 1
        metadata = bytes(self.device.memory[0x31000:0x31034])
        with self.assertRaises(ProtocolError):
            self.finish()
        self.rescue()
        self.assertEqual(bytes(self.device.memory[0x31000:0x31034]), metadata)
        self.assertEqual(self.device.commands[-1], 7)

    def test_transport_failure_never_retries_or_resets(self):
        for command in (4, 3, 0xC3):
            with self.subTest(command=command):
                self.device = ControllerBoot()
                self.device.fail_command = command
                with self.assertRaises(OSError):
                    self.rescue()
                self.assertEqual(self.device.commands[-1], command)
                self.assertEqual(self.device.commands.count(command), 1)
                self.assertNotIn(7, self.device.commands)

    def test_crc_and_readback_failures_block_reset(self):
        for field in ('bad_crc', 'corrupt_readback', 'change_metadata'):
            with self.subTest(field=field):
                self.device = ControllerBoot()
                setattr(self.device, field, True)
                with self.assertRaises(FirmwareMismatch):
                    self.rescue()
                self.assertNotIn(7, self.device.commands)

    def test_failure_in_later_sector_leaves_first_sector_erased(self):
        self.large_image()
        self.device.bad_crc = True
        with self.assertRaises(FirmwareMismatch):
            self.rescue()
        self.assertEqual(bytes(self.device.memory[0x5000:0x6000]), b'\xff' * 4096)
        self.assertNotIn(7, self.device.commands)

    def test_rescue_command_bounds_reject_before_io(self):
        for command, args in (
            (4, {'address': 0, 'count': 4096}),
            (4, {'address': 0x5001, 'count': 4096}),
            (4, {'address': 0x31000, 'count': 4096}),
            (4, {'address': 0x5000, 'count': 8192}),
            (3, {'address': 0x5000, 'data': bytes(47)}),
            (3, {'address': 0x30FF0, 'data': bytes(32)}),
            (0xC3, {'address': 0x5000, 'count': 257}),
            (0xC4, {'address': 0x5000}),
        ):
            with self.subTest(command=command, args=args):
                with self.assertRaises(UnsupportedProtocol):
                    recovery._rescue_exchange(self.device, command, **args)
        self.assertEqual(self.device.sent, [])

    def test_wrong_mutation_ack_stops_immediately(self):
        for field in range(6):
            with self.subTest(field=field):
                self.device = ControllerBoot()
                self.device.bad_mutation_header = field
                with self.assertRaises(ProtocolError):
                    self.rescue()
                self.assertEqual(self.device.commands[-1], 4)
                self.assertEqual(self.device.commands.count(4), 1)
                self.assertNotIn(3, self.device.commands)
                self.assertNotIn(7, self.device.commands)

    def test_unreviewed_boot_policy_and_oversize_image_fail_before_io(self):
        with patch.object(recovery, 'REVIEWED_BOOT_SHA256', '0' * 64):
            with self.assertRaises(UnsupportedProtocol):
                self.rescue()
        payload = application(110, 0x2C004)
        self.device.image = parse_firmware(struct.pack('<7I', 110, 0x5000, len(payload), 0x6012, 0, 0, 0) + payload)
        self.device.authorization = replace(self.device.authorization, image_sha256=self.device.image.sha256)
        with self.assertRaises(UnsupportedProtocol):
            self.rescue()
        self.assertEqual(self.device.sent, [])


if __name__ == '__main__':
    unittest.main()
