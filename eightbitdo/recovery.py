"""Bounded receiver boot inspection and vendor replay; see docs/receiver-recovery.md.

Callers must hold the device/update locks, inhibit suspend, and supply a boot
transport bound to the original USB port and reviewed unnumbered descriptor.
No authorization record comes from UI or downloaded catalog arguments.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .protocol import (
    BLOCK_SIZE, HEADER, DeviceIdentity, FirmwareImage, FirmwareMismatch,
    ProtocolError, ReportTransport, SourceReviewedAuthorization, UnsupportedProtocol,
    _validate_flash_authorization, crc16,
)


@dataclass(frozen=True)
class RecoveryStatus:
    action: str
    reason: str
    evidence_level: str = "source-reviewed-experimental"

    @property
    def can_reset(self) -> bool:
        return self.action in ("finish-pending", "return-installed")


@dataclass(frozen=True)
class RecoveryResult:
    action: str
    vendor_copy_expected: bool
    needs_reconnect_verification: bool = True


def _request(command: int, *, length: int = 0, offset: int = 0) -> bytes:
    if command not in (5, 7, 8) or not 0 <= length <= 32 or not 0 <= offset < 0x1F000:
        raise UnsupportedProtocol("Recovery permits only bounded reads, product identity, and reset")
    payload = b"\x05" + HEADER.pack(command, 0, length, 0, length, offset)
    return b"\0" + payload.ljust(64, b"\0")


def _exchange(transport: ReportTransport, command: int, *, length: int, offset: int = 0) -> bytes:
    transport.send(_request(command, length=length, offset=offset))
    reply = transport.receive(2.0)
    if len(reply) != 64 or reply[:2] != b"\x00\x05":
        raise ProtocolError("Unexpected receiver boot reply framing")
    expected = (0, command, length, 0, length, offset)
    if HEADER.unpack_from(reply, 2) != expected:
        raise ProtocolError("Receiver boot acknowledgment does not match the request")
    return reply[18:18 + length]


def _read(transport: ReportTransport, address: int, length: int) -> bytes:
    if length <= 0 or address < 0 or address + length > 0x1F000:
        raise UnsupportedProtocol("Recovery read is outside the reviewed receiver flash bounds")
    result = bytearray()
    for position in range(0, length, 32):
        count = min(32, length - position)
        result.extend(_exchange(transport, 5, length=count, offset=address + position))
    return bytes(result)


def _bootable_application(data: bytes) -> bool:
    if len(data) < 36 or data[8:12] != b"KNLT" or int.from_bytes(data[28:30], "little") != 0x6013:
        return False
    extent = int.from_bytes(data[24:28], "little")
    # Require the checksummed layout even though the vendor can skip its checksum.
    if data[30] != 1 or extent < 32 or extent % 4 or extent + 4 > len(data):
        return False
    if not 0 < int.from_bytes(data[2:6], "little") < 0xFFFF:
        return False
    if int.from_bytes(data[12:14], "little") * 16 > 0x7200:
        return False
    return sum(data[:extent - 4]) == int.from_bytes(data[extent:extent + 4], "little")


def inspect_recovery(
    transport: ReportTransport, image: FirmwareImage, *, authorization: SourceReviewedAuthorization,
) -> RecoveryStatus:
    """Read-only inspection; a successful result is not a completed update.

    Source versions identify the reviewed policy, not an inferred boot-mode
    firmware version. Boot identity and actual bytes establish the device state.
    """
    if type(authorization) is not SourceReviewedAuthorization or authorization.product_id != 0x6013:
        raise UnsupportedProtocol("Recovery is reviewed only for source-bound Ultimate 2 receivers")
    if not authorization.source_versions:
        raise UnsupportedProtocol("Recovery requires a reviewed source firmware policy")
    regions = _validate_flash_authorization(
        image,
        DeviceIdentity(0x6013, authorization.source_versions[0], hardware_revision=authorization.hardware_revision),
        authorization,
    )
    if not _bootable_application(image.payload):
        raise FirmwareMismatch("Authorized image does not satisfy the reviewed bootloader checks")
    if _exchange(transport, 8, length=2) != b"\x13\x60":
        raise FirmwareMismatch("Boot product identity is not the reviewed receiver")
    boot, source_app = regions
    if hashlib.sha256(_read(transport, boot.address, boot.length)).hexdigest() != boot.sha256:
        raise FirmwareMismatch("Receiver bootloader does not match the reviewed fingerprint")
    metadata = _read(transport, 0x12000, 52)
    erased = metadata == b"\xff" * 52
    if not erased and crc16(metadata[:48]) != struct.unpack_from("<I", metadata, 48)[0]:
        raise ProtocolError("Commit metadata is damaged; automatic recovery is unavailable")
    pending = not erased and struct.unpack_from("<I", metadata, 24)[0] == 0xAEB14D6C
    if pending:
        version = struct.unpack_from("<I", metadata, 12)[0]
        staging, length = struct.unpack_from("<II", metadata, 28)
        if (version, staging, length) != (image.version_raw, 0x13000, len(image.payload)):
            raise FirmwareMismatch("Pending metadata does not match the authorized image")
        rounded = (length + BLOCK_SIZE - 1) // BLOCK_SIZE * BLOCK_SIZE
        staged = _read(transport, staging, rounded)
        expected = image.payload + b"\xff" * (rounded - length)
        if staged != expected:
            raise FirmwareMismatch("Pending staging bytes or sector padding differ from the authorized image")
        return RecoveryStatus("finish-pending", "Verified committed image can be replayed by the vendor bootloader")
    source = _read(transport, source_app.address, source_app.length)
    if hashlib.sha256(source).hexdigest() == source_app.sha256 and _bootable_application(source):
        return RecoveryStatus("return-installed", "Reviewed installed firmware remains intact")
    target = _read(transport, 0x5000, len(image.payload))
    if target == image.payload and _bootable_application(target):
        return RecoveryStatus("return-installed", "Authorized target firmware is installed and valid")
    return RecoveryStatus("blocked", "No intact reviewed application or verified pending image is available")


def finish_recovery(
    transport: ReportTransport, image: FirmwareImage, *, authorization: SourceReviewedAuthorization,
) -> RecoveryResult:
    """Reinspect all evidence and send reset once, without initializing or writing.

    A pending record makes this reset invoke the vendor's flash-copy procedure.
    The caller must verify reconnect identity before declaring recovery complete.
    """
    status = inspect_recovery(transport, image, authorization=authorization)
    if not status.can_reset:
        raise UnsupportedProtocol(status.reason)
    transport.send(_request(7))
    return RecoveryResult(status.action, status.action == "finish-pending")
