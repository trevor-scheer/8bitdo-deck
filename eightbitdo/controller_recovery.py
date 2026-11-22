"""Source-reviewed Ultimate 2 controller USB boot recovery, never radio transport.

The caller owns device/update locks, suspend inhibition and original-port binding
to the reviewed unnumbered 64-byte boot HID descriptor. Authorizations must come
from compiled policy, not UI input. See docs/devices/ultimate2-controller-recovery.md.
"""

from __future__ import annotations

import hashlib
import struct

from .protocol import (
    BLOCK_SIZE, HEADER, DeviceIdentity, FirmwareImage, FirmwareMismatch, FlashResult,
    ProtocolError, ReportTransport, SourceReviewedAuthorization, UnsupportedProtocol,
    _validate_flash_authorization, crc16,
)
from .recovery import RecoveryResult, RecoveryStatus


REVIEWED_BOOT_SHA256 = "faab5fff75e88556749d15980f84926592fc04329da2866e9af36919af529fa5"
APPLICATION_BASE = 0x5000
METADATA_BASE = 0x31000
STAGING_BASE = 0x32000
READ_LIMIT = 0x5E000


def _request(command: int, *, length: int = 0, offset: int = 0) -> bytes:
    if command not in (5, 7, 8) or not 0 <= length <= 32 or not 0 <= offset < READ_LIMIT:
        raise UnsupportedProtocol("Controller inspection permits only bounded reads, identity and reset")
    return b"\0" + (b"\x05" + HEADER.pack(command, 0, length, 0, length, offset)).ljust(64, b"\0")


def _exchange(transport: ReportTransport, command: int, *, length: int, offset: int = 0) -> bytes:
    transport.send(_request(command, length=length, offset=offset))
    reply = transport.receive(2.0)
    if len(reply) != 64 or reply[:2] != b"\x00\x05":
        raise ProtocolError("Unexpected controller boot reply framing")
    if HEADER.unpack_from(reply, 2) != (0, command, length, 0, length, offset):
        raise ProtocolError("Controller boot acknowledgment does not match the request")
    return reply[18:18 + length]


def _read(transport: ReportTransport, address: int, length: int) -> bytes:
    if length <= 0 or address < 0 or address + length > READ_LIMIT:
        raise UnsupportedProtocol("Controller read is outside the reviewed flash bounds")
    return b"".join(
        _exchange(transport, 5, length=min(32, length - position), offset=address + position)
        for position in range(0, length, 32)
    )


def _bootable_application(data: bytes) -> bool:
    """Require both checksums, including the controller's secondary component."""
    if len(data) < 72 or data[8:12] != b"KNLT" or data[28:30] != b"\x12\x60":
        return False
    primary_length = int.from_bytes(data[24:28], "little")
    if data[30] != 1 or primary_length < 32 or primary_length % 4 or primary_length + 40 > len(data):
        return False
    if not 0 < int.from_bytes(data[2:6], "little") < 0xFFFF:
        return False
    if sum(data[:primary_length - 4]) != int.from_bytes(data[primary_length:primary_length + 4], "little"):
        return False
    secondary_start = primary_length + 4
    secondary_length = int.from_bytes(data[primary_length + 24:primary_length + 28], "little")
    secondary_end = secondary_start + secondary_length
    if secondary_length < 36 or secondary_length % 4 or secondary_end + 4 > len(data):
        return False
    return sum(data[secondary_start:secondary_end]) == int.from_bytes(data[secondary_end:secondary_end + 4], "little")


def _validate_policy(image: FirmwareImage, authorization: SourceReviewedAuthorization):
    if type(authorization) is not SourceReviewedAuthorization or authorization.product_id != 0x6012:
        raise UnsupportedProtocol("Controller recovery requires a source-reviewed Ultimate 2 controller authorization")
    if not authorization.source_versions:
        raise UnsupportedProtocol("Controller recovery requires reviewed source firmware versions")
    regions = _validate_flash_authorization(
        image, DeviceIdentity(0x6012, authorization.source_versions[0], hardware_revision=authorization.hardware_revision),
        authorization,
    )
    if regions[0].sha256 != REVIEWED_BOOT_SHA256:
        raise UnsupportedProtocol("Controller bootloader protocol has not been reviewed for this fingerprint")
    if not _bootable_application(image.payload):
        raise FirmwareMismatch("Authorized controller image fails primary or secondary boot checksums")
    return regions


def _inspect_boot(
    transport: ReportTransport, image: FirmwareImage, authorization: SourceReviewedAuthorization,
    *, allow_damaged_metadata: bool = False,
):
    boot, source = _validate_policy(image, authorization)
    if _exchange(transport, 8, length=2) != b"\x12\x60":
        raise FirmwareMismatch("Boot product identity is not the reviewed controller")
    if hashlib.sha256(_read(transport, boot.address, boot.length)).hexdigest() != boot.sha256:
        raise FirmwareMismatch("Controller bootloader does not match the reviewed fingerprint")
    metadata = _read(transport, METADATA_BASE, 52)
    erased = metadata == b"\xff" * 52
    valid = not erased and crc16(metadata[:48]) == struct.unpack_from("<I", metadata, 48)[0]
    if not erased and not valid and not allow_damaged_metadata:
        raise ProtocolError("Controller commit metadata is damaged; recovery is blocked")
    pending = valid and struct.unpack_from("<I", metadata, 24)[0] == 0xAEB14D6C
    return source, metadata, pending


def inspect_controller_recovery(
    transport: ReportTransport, image: FirmwareImage, *, authorization: SourceReviewedAuthorization,
) -> RecoveryStatus:
    """Read-only inspection; boot mode cannot establish the original runtime version."""
    source, metadata, pending = _inspect_boot(transport, image, authorization)
    if pending:
        version = struct.unpack_from("<I", metadata, 12)[0]
        staging, length = struct.unpack_from("<II", metadata, 28)
        if (version, staging, length) != (image.version_raw, STAGING_BASE, len(image.payload)):
            raise FirmwareMismatch("Controller pending metadata does not match the authorized image")
        rounded = (length + BLOCK_SIZE - 1) // BLOCK_SIZE * BLOCK_SIZE
        if _read(transport, staging, rounded) != image.payload + b"\xff" * (rounded - length):
            raise FirmwareMismatch("Controller pending image or sector padding differs from authorized bytes")
        return RecoveryStatus("finish-pending", "Verified controller staging can be replayed by the vendor bootloader")
    installed = _read(transport, source.address, source.length)
    if hashlib.sha256(installed).hexdigest() == source.sha256 and _bootable_application(installed):
        return RecoveryStatus("return-installed", "Reviewed controller source image and both checksums remain intact")
    target = _read(transport, APPLICATION_BASE, len(image.payload))
    if target == image.payload and _bootable_application(target):
        return RecoveryStatus("return-installed", "Authorized controller target and both checksums are installed")
    return RecoveryStatus("blocked", "No intact reviewed controller application or verified pending image")


def finish_controller_recovery(
    transport: ReportTransport, image: FirmwareImage, *, authorization: SourceReviewedAuthorization,
) -> RecoveryResult:
    """Repeat inspection, reset once, and require caller-owned reconnect verification.

    Reset with a pending image invokes flash copying inside the vendor bootloader.
    Send failures propagate without retry; a reset acknowledgment is not required.
    """
    status = inspect_controller_recovery(transport, image, authorization=authorization)
    if not status.can_reset:
        raise UnsupportedProtocol(status.reason)
    transport.send(_request(7))
    return RecoveryResult(status.action, status.action == "finish-pending")


def _rescue_exchange(
    transport: ReportTransport, command: int, *, address: int, data: bytes = b"", count: int = 0,
) -> bytes:
    """The boot dispatcher uses absolute addresses and differs from the application."""
    if command == 4:
        if data or count != BLOCK_SIZE or address % BLOCK_SIZE:
            raise UnsupportedProtocol("Controller rescue erase requires exactly one aligned sector")
        length, checksum, total, offset = 0, 0, BLOCK_SIZE, address
        response_length, response_total, payload_size = 0, 0, 0
    elif command == 3:
        if count or not 1 <= len(data) <= 46:
            raise UnsupportedProtocol("Controller rescue write requires at most 46 bytes")
        length, checksum, total, offset = len(data), crc16(data), len(data), address
        count = len(data)
        response_length, response_total, payload_size = length, total, 0
    elif command == 0xC3:
        if data or not 1 <= count <= 256:
            raise UnsupportedProtocol("Controller boot CRC is bounded to 256 bytes")
        data = struct.pack("<III", address, count, BLOCK_SIZE)
        length, checksum, total, offset = 12, crc16(data), 12, 0
        response_length, response_total, payload_size = 12, 12, 2
    else:
        raise UnsupportedProtocol("Unsupported controller rescue command")
    if address < APPLICATION_BASE or address + count > METADATA_BASE:
        raise UnsupportedProtocol("Controller rescue cannot modify bootloader, metadata or staging")
    report = b"\0" + (b"\x05" + HEADER.pack(command, 0, length, checksum, total, offset) + data).ljust(64, b"\0")
    transport.send(report)
    reply = transport.receive(10.0 if command == 4 else 2.0)
    if len(reply) != 64 or reply[:2] != b"\x00\x05":
        raise ProtocolError("Unexpected controller rescue reply framing")
    if HEADER.unpack_from(reply, 2) != (0, command, response_length, checksum, response_total, offset):
        raise ProtocolError("Controller rescue acknowledgment does not match the request")
    return reply[18:18 + payload_size]


def _verify_rescue_sector(transport: ReportTransport, address: int, expected: bytes) -> None:
    if len(expected) != BLOCK_SIZE or address % BLOCK_SIZE:
        raise UnsupportedProtocol("Controller rescue verification requires a complete aligned sector")
    for position in range(0, BLOCK_SIZE, 256):
        chunk = expected[position:position + 256]
        reply = _rescue_exchange(transport, 0xC3, address=address + position, count=len(chunk))
        if struct.unpack("<H", reply)[0] != crc16(chunk):
            raise FirmwareMismatch("Controller rescue sector CRC mismatch")
    if _read(transport, address, BLOCK_SIZE) != expected:
        raise FirmwareMismatch("Controller rescue sector readback differs from authorized bytes")


def inspect_controller_rescue(
    transport: ReportTransport, image: FirmwareImage, *, authorization: SourceReviewedAuthorization,
) -> RecoveryStatus:
    """Read-only full-reinstall eligibility, including incomplete applications.

    This does not promise that the existing application can run, or authorize a
    reset. A separate explicit confirmation and full reinspection precede erase.
    """
    _, _, pending = _inspect_boot(transport, image, authorization, allow_damaged_metadata=True)
    if pending:
        return RecoveryStatus("blocked", "Controller has pending firmware; inspect its replay path before USB reinstall")
    return RecoveryStatus("reflash", "Reviewed USB bootloader can reinstall the complete authorized controller image")


def flash_controller_recovery(
    transport: ReportTransport, image: FirmwareImage, *, authorization: SourceReviewedAuthorization,
    confirmed: bool = False,
) -> FlashResult:
    """Explicit full USB reinstall; pending commits must use reviewed replay instead.

    Invalid metadata CRC is ignored by this exact bootloader (0x0ae8); preserve
    the bytes and require them unchanged before reset. Do not attempt repair of
    the metadata sector. This operation remains unqualified on physical hardware.
    """
    if confirmed is not True:
        raise UnsupportedProtocol("Controller USB erase and reinstall requires explicit confirmation")
    _, metadata, pending = _inspect_boot(transport, image, authorization, allow_damaged_metadata=True)
    if pending:
        raise UnsupportedProtocol("Controller has pending firmware; inspect its replay path before USB reinstall")
    rounded = (len(image.payload) + BLOCK_SIZE - 1) // BLOCK_SIZE * BLOCK_SIZE
    expected = image.payload + b"\xff" * (rounded - len(image.payload))

    # Keep the activation/header sector erased until every later sector verifies.
    # The host never resets on failure; the forced USB-boot flag is left intact.
    _rescue_exchange(transport, 4, address=APPLICATION_BASE, count=BLOCK_SIZE)
    order = list(range(BLOCK_SIZE, rounded, BLOCK_SIZE)) + [0]
    for offset in order:
        address = APPLICATION_BASE + offset
        if offset:
            _rescue_exchange(transport, 4, address=address, count=BLOCK_SIZE)
        block = image.payload[offset:offset + BLOCK_SIZE]
        for position in range(0, len(block), 46):
            _rescue_exchange(transport, 3, address=address + position, data=block[position:position + 46])
        _verify_rescue_sector(transport, address, expected[offset:offset + BLOCK_SIZE])

    # Check every byte again after all programming, including erased padding.
    for offset in range(0, rounded, BLOCK_SIZE):
        _verify_rescue_sector(transport, APPLICATION_BASE + offset, expected[offset:offset + BLOCK_SIZE])
    if _read(transport, METADATA_BASE, 52) != metadata:
        raise FirmwareMismatch("Controller metadata changed during USB rescue; reset is blocked")
    if hashlib.sha256(_read(transport, 0, APPLICATION_BASE)).hexdigest() != REVIEWED_BOOT_SHA256:
        raise FirmwareMismatch("Controller bootloader changed during USB rescue; reset is blocked")
    transport.send(_request(7))
    return FlashResult(image.product_id, image.version, len(image.payload))
