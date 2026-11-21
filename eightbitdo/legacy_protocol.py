"""Ultimate Bluetooth receiver boot protocol, traced in the official updater.

The service must qualify the image, initialization tuple, boot transition, and
recovery before using this backend. No service route enables it implicitly.
See docs/legacy-receiver-protocol.md for source addresses and remaining limits.
"""

from __future__ import annotations

import errno
import hashlib
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

from .protocol import (
    FIRMWARE_HEADER,
    HEADER,
    DeviceIdentity,
    FirmwareImage,
    FirmwareMismatch,
    FlashAuthorization,
    FlashQualification,
    FlashResult,
    ProtocolError,
    ProtocolTimeout,
    ReportTransport,
    UnsupportedProtocol,
    crc16,
    query_identity,
)


RECEIVER_PID = 0x6008
RUNTIME_PID = 0x3109
BOOT_PIDS = frozenset({0x3208, 0x3002})
PAGE_SIZE = 4096
WRITE_SIZE = 32
BOOT_ENTRY_REPORT = bytes.fromhex("81 05 00 51 00").ljust(64, b"\0")


@dataclass(frozen=True)
class ReceiverInitialization:
    image_offset: int
    upgrade_length: int
    upgrade_erase_offset: int
    erase_size: int

    @classmethod
    def from_payload(cls, payload: bytes) -> ReceiverInitialization:
        if len(payload) != 16:
            raise ProtocolError("Receiver initialization must contain four 32-bit fields")
        return cls(*struct.unpack("<4I", payload))


@dataclass(frozen=True)
class LegacySourceReviewedAuthorization(FlashAuthorization):
    """Developer-reviewed compatibility; not a successful hardware update record.

    Prefix digests bind the packaged boot code reviewed across source versions.
    They do not claim the installed receiver's boot code has been read back.
    """

    initialization: ReceiverInitialization | None = None
    boot_prefix_sha256: str = ""
    boot_prefix_crc16: int | None = None
    evidence_level: str = field(default="source-reviewed-experimental", init=False)


@dataclass(frozen=True)
class ReceiverFlashPlan:
    image_offset: int
    data: bytes

    @property
    def page_count(self) -> int:
        return len(self.data) // PAGE_SIZE


def build_boot_command(
    command: int,
    *,
    data: bytes = b"",
    length: int | None = None,
    total_length: int = 0,
    offset: int = 0,
) -> bytes:
    """Return hidraw's zero report selector plus the 64-byte unnumbered packet."""
    requested_length = len(data) if length is None else length
    if len(data) > 46 or not 0 <= requested_length <= 46:
        raise ProtocolError("Receiver HID payload exceeds 46 bytes")
    if data and requested_length != len(data):
        raise ProtocolError("Receiver payload length does not match the data")
    try:
        header = HEADER.pack(command, 0, requested_length, crc16(data), total_length, offset)
    except (struct.error, OverflowError) as error:
        raise ProtocolError("Receiver command field is outside its integer range") from error
    return (b"\0\x05" + header + data).ljust(65, b"\0")


def parse_boot_reply(report: bytes, *, command: int, payload_size: int) -> bytes:
    """Parse firmware bytes returned by hidraw, without removing their first byte.

    The native updater checks status and command only. Its reply header does not
    establish that length, CRC, total, and offset echo the request.
    """
    if not 0 <= payload_size <= 46 or not 18 + payload_size <= len(report) <= 64:
        raise ProtocolError("Receiver boot reply is truncated or oversized")
    if report[:2] != b"\0\x05":
        raise ProtocolError("Not an unnumbered receiver boot reply")
    status, echoed_command = struct.unpack_from("<HH", report, 2)
    if echoed_command != command:
        raise ProtocolError("Receiver acknowledged a different command")
    if status:
        raise ProtocolError(f"Receiver rejected command {command:04x} with status {status:04x}")
    return report[18:18 + payload_size]


def _exchange(
    transport: ReportTransport,
    command: int,
    *,
    payload_size: int = 0,
    timeout: float = 2.0,
    **fields: object,
) -> bytes:
    if not 0 < timeout <= 60:
        raise ProtocolError("Timeout must be between zero and 60 seconds")
    transport.send(build_boot_command(command, **fields))
    return _receive_reply(transport, command, payload_size=payload_size, timeout=timeout)


def _receive_reply(
    transport: ReportTransport, command: int, *, payload_size: int = 0, timeout: float = 2.0,
) -> bytes:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            report = transport.receive(remaining)
        except TimeoutError as error:
            raise ProtocolTimeout("Receiver did not acknowledge its boot command") from error
        if not report:
            break
        if report.startswith(b"\0\x05"):
            return parse_boot_reply(report, command=command, payload_size=payload_size)
    raise ProtocolTimeout("Receiver did not acknowledge its boot command")


def query_boot_target(transport: ReportTransport, *, usb_pid: int) -> int:
    if usb_pid not in BOOT_PIDS:
        raise UnsupportedProtocol("USB device is not a supported receiver boot interface")
    payload = _exchange(transport, 8, length=2, total_length=2, payload_size=2)
    return int.from_bytes(payload, "little")


def request_receiver_boot(transport: ReportTransport, *, identity: DeviceIdentity) -> None:
    """Request the vendor boot transition; caller must reconnect and match its USB port."""
    if identity.product_id != RECEIVER_PID or not identity.target_verified:
        raise UnsupportedProtocol("Boot transition requires a verified Ultimate Bluetooth receiver")
    if query_identity(transport, usb_pid=RUNTIME_PID) != identity:
        raise FirmwareMismatch("Receiver identity changed before entering its bootloader")
    transport.send(BOOT_ENTRY_REPORT)


def _reset_boot(transport: ReportTransport) -> bool:
    # Keep input polling active after sending: closing HID immediately can leave
    # this receiver in boot mode. Its reset can also disconnect before ACK delivery.
    transport.send(build_boot_command(7))
    try:
        _receive_reply(transport, 7)
    except OSError as error:
        if error.errno not in (errno.EIO, errno.ENODEV, errno.ENXIO):
            raise
        return False
    return True


def reset_receiver_boot(transport: ReportTransport, *, usb_pid: int) -> bool:
    """Reset a verified receiver without initializing, erasing, or saving firmware.

    Keep HID open until ACK or a reset disconnect. Return whether ACK was received;
    either result requires the caller to verify same-port application identity and
    version. Send errors, timeouts, malformed replies, and unrelated receive errors
    propagate without retrying reset.
    """
    if query_boot_target(transport, usb_pid=usb_pid) != RECEIVER_PID:
        raise FirmwareMismatch("Bootloader belongs to another 8BitDo model")
    return _reset_boot(transport)


def _validate_image(image: FirmwareImage) -> None:
    if image.product_id != RECEIVER_PID:
        raise FirmwareMismatch("Firmware is not for the Ultimate Bluetooth receiver")
    if (
        image.address != 0x3000
        or not 3 * PAGE_SIZE <= len(image.payload) <= 0x40000
        or len(image.payload) % PAGE_SIZE
        or image.payload[16:22] != b"80NA_R"
        or image.reserved1 or image.revision or image.reserved2
    ):
        raise UnsupportedProtocol("Unsupported Ultimate Bluetooth receiver image layout")
    encoded = FIRMWARE_HEADER.pack(
        image.version_raw, image.address, len(image.payload), image.product_id,
        image.reserved1, image.revision, image.reserved2,
    ) + image.payload
    if hashlib.sha256(encoded).hexdigest() != image.sha256:
        raise FirmwareMismatch("Receiver firmware changed after it was parsed")


def plan_receiver_flash(image: FirmwareImage, initialization: ReceiverInitialization) -> ReceiverFlashPlan:
    """Map DAT bytes to receiver-relative flash offsets, without device operations."""
    _validate_image(image)
    skip = initialization.image_offset
    if (
        initialization.erase_size != PAGE_SIZE
        or skip <= 0 or skip % PAGE_SIZE
        or skip > len(image.payload) - 2 * PAGE_SIZE
    ):
        raise UnsupportedProtocol("Unsupported receiver initialization geometry")
    # Native a4e0 uses upgrade_addr as a DAT prefix length; the destination is
    # relative offset zero. The other two initialization fields are not used.
    return ReceiverFlashPlan(skip, image.payload[skip:])


def validate_receiver_authorization(
    image: FirmwareImage,
    identity: DeviceIdentity,
    authorization: FlashQualification | LegacySourceReviewedAuthorization,
    *,
    expected_initialization: ReceiverInitialization | None = None,
) -> ReceiverInitialization:
    """Validate an experimental or hardware binding without opening any device."""
    _validate_image(image)
    if type(authorization) not in (FlashQualification, LegacySourceReviewedAuthorization):
        raise UnsupportedProtocol("Receiver writes require an explicit reviewed authorization type")
    if isinstance(authorization, LegacySourceReviewedAuthorization):
        if expected_initialization is None:
            expected_initialization = authorization.initialization
        if authorization.initialization is None or authorization.initialization != expected_initialization:
            raise FirmwareMismatch("Receiver initialization is not bound to the source review")
        # This is artifact verification, not a claim of live bootloader readback.
        prefix = image.payload[:expected_initialization.image_offset]
        if (
            not prefix
            or hashlib.sha256(prefix).hexdigest() != authorization.boot_prefix_sha256
            or crc16(prefix) != authorization.boot_prefix_crc16
        ):
            raise FirmwareMismatch("Receiver boot prefix differs from the source-reviewed artifact")
    if expected_initialization is None:
        raise UnsupportedProtocol("Receiver hardware qualification must supply its initialization tuple")
    if identity.product_id != RECEIVER_PID or not identity.target_verified:
        raise UnsupportedProtocol("Receiver update requires a verified runtime identity")
    if (
        authorization.product_id != RECEIVER_PID
        or authorization.hardware_revision != identity.hardware_revision
        or authorization.image_revision != image.revision
        or authorization.image_sha256 != image.sha256
        or not authorization.evidence.strip()
        or identity.version_raw not in authorization.source_versions
        or identity.beta != 0
    ):
        raise FirmwareMismatch("Receiver firmware, identity, and authorization do not match")
    plan_receiver_flash(image, expected_initialization)
    return expected_initialization


def flash_boot_receiver(
    transport: ReportTransport,
    image: FirmwareImage,
    *,
    identity: DeviceIdentity,
    boot_usb_pid: int,
    qualification: FlashQualification | LegacySourceReviewedAuthorization,
    expected_initialization: ReceiverInitialization | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> FlashResult:
    """Update an already connected, externally qualified receiver boot interface.

    The service owns qualification, device locking, suspend inhibition, USB port
    matching, recovery instructions, and post-reset version verification. C1
    configures the bootloader update session. Failures never retry destructive
    commands, finalize, or reset automatically.
    """
    expected_initialization = validate_receiver_authorization(
        image, identity, qualification, expected_initialization=expected_initialization,
    )
    plan = plan_receiver_flash(image, expected_initialization)
    if query_boot_target(transport, usb_pid=boot_usb_pid) != RECEIVER_PID:
        raise FirmwareMismatch("Bootloader belongs to another 8BitDo model")
    notify = progress or (lambda stage, done, total: None)
    total = len(plan.data)
    notify("preparing", 0, total)
    observed = ReceiverInitialization.from_payload(_exchange(transport, 0xC1, payload_size=16))
    if observed != expected_initialization:
        raise FirmwareMismatch("Receiver initialization differs from its qualification")
    for offset in range(0, total, PAGE_SIZE):
        notify("erasing", offset, total)
        _exchange(transport, 4, total_length=PAGE_SIZE, offset=offset, timeout=30.0)
    for offset in range(0, total, WRITE_SIZE):
        chunk = plan.data[offset:offset + WRITE_SIZE]
        _exchange(transport, 3, data=chunk, total_length=total, offset=offset)
        notify("writing", offset + len(chunk), total)

    def verify_page(index: int) -> None:
        offset = index * PAGE_SIZE
        notify("verifying", offset, total)
        payload = struct.pack("<III", offset, PAGE_SIZE, 0)
        observed_crc = _exchange(transport, 0xC3, data=payload, total_length=12, payload_size=2)
        expected_crc = crc16(plan.data[offset:offset + PAGE_SIZE])
        if int.from_bytes(observed_crc, "little") != expected_crc:
            raise ProtocolError(f"Receiver firmware CRC failed at relative offset {offset:#x}")

    for index in range(2, plan.page_count):
        verify_page(index)
    notify("committing", total, total)
    _exchange(transport, 0xC2)
    verify_page(0)
    verify_page(1)
    notify("restarting", total, total)
    _reset_boot(transport)
    return FlashResult(RECEIVER_PID, image.version, total)
