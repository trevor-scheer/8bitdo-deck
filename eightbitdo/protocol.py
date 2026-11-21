"""8BitDo application-mode HID protocol; see docs/protocol.md for evidence.

HID reports include their report ID. The application must qualify hardware and
own the device lock and suspend inhibitor before calling flash_application.
"""

from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass
from typing import Callable, Protocol


REPORT_SIZE = 64
PAYLOAD_SIZE = 46
BLOCK_SIZE = 4096
MAX_IMAGE_SIZE = 8 * 1024 * 1024
MODERN_PIDS = frozenset({0x6012, 0x6013})
IDENTIFY_PIDS = frozenset({0x3109, 0x310B, 0x6012, 0x6013})
HEADER = struct.Struct("<HHHHII")
FIRMWARE_HEADER = struct.Struct("<7I")
IDENTIFY_REPORT = bytes.fromhex("81 05 00 21 01").ljust(REPORT_SIZE, b"\0")


class ProtocolError(ValueError):
    """The device or firmware did not satisfy the protocol contract."""


class ProtocolTimeout(ProtocolError):
    """No matching report arrived before the deadline."""


class UnsupportedProtocol(ProtocolError):
    """The requested operation is not supported for this target."""


class FirmwareMismatch(ProtocolError):
    """The firmware does not match the selected target."""


class ReportTransport(Protocol):
    def send(self, report: bytes) -> None: ...

    def receive(self, timeout: float) -> bytes: ...


@dataclass(frozen=True)
class DeviceIdentity:
    product_id: int
    version_raw: int
    beta: int = 0
    hardware_revision: int | None = None
    target_verified: bool = True

    @property
    def pid(self) -> int:
        return self.product_id

    @property
    def version(self) -> str:
        return format_version(self.version_raw)


@dataclass(frozen=True)
class PairedControllerInfo:
    version_raw: int
    beta: int
    battery_percent: int | None
    edition: int

    @property
    def version(self) -> str:
        return format_version(self.version_raw)


@dataclass(frozen=True)
class FirmwareImage:
    version_raw: int
    address: int
    product_id: int
    reserved1: int
    revision: int
    reserved2: int
    payload: bytes
    sha256: str

    @property
    def version(self) -> str:
        return format_version(self.version_raw & 0xFFFF)

    @property
    def pid(self) -> int:
        return self.product_id


@dataclass(frozen=True)
class FlashQualification:
    """Developer-issued evidence binding, never constructed from UI arguments.

The registry must require a real hardware qualification record before creating
this object. A nonempty evidence string alone is not hardware verification.
"""

    product_id: int
    hardware_revision: int | None
    image_revision: int
    image_sha256: str
    evidence: str
    source_versions: tuple[int, ...] = ()


@dataclass(frozen=True)
class FlashResult:
    product_id: int
    version: str
    bytes_verified: int
    needs_reconnect_verification: bool = True


@dataclass(frozen=True)
class CommandReply:
    status: int
    command: int
    length: int
    crc: int
    total_length: int
    offset: int
    payload: bytes


def format_version(version: int) -> str:
    if not 0 <= version <= 0xFFFF:
        raise ProtocolError("Firmware version is outside the 16-bit range")
    return f"{version // 100}.{version % 100:02d}"


def crc16(data: bytes) -> int:
    """CRC-16/MODBUS: reflected 0xA001 polynomial and 0xFFFF initial value."""
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def build_command(
    command: int,
    *,
    parameters: int = 0,
    data: bytes = b"",
    length: int | None = None,
    total_length: int = 0,
    offset: int = 0,
) -> bytes:
    if len(data) > PAYLOAD_SIZE:
        raise ProtocolError("HID payload exceeds 46 bytes")
    payload_length = len(data) if length is None else length
    if not 0 <= payload_length <= PAYLOAD_SIZE:
        raise ProtocolError("Invalid HID payload length")
    if data and payload_length != len(data):
        raise ProtocolError("HID payload length does not match the data")
    try:
        header = HEADER.pack(
            command, parameters, payload_length, crc16(data) if data else 0,
            total_length, offset,
        )
    except (struct.error, OverflowError) as error:
        raise ProtocolError("Command field is outside its integer range") from error
    return (b"\x81\x05" + header + data).ljust(REPORT_SIZE, b"\0")


def parse_command_reply(report: bytes) -> CommandReply:
    if not 18 <= len(report) <= REPORT_SIZE or report[:2] != b"\x02\x05":
        raise ProtocolError("Not an application-mode class-05 reply")
    status, command, length, checksum, total, offset = HEADER.unpack_from(report, 2)
    if length > PAYLOAD_SIZE or len(report) < 18 + length:
        raise ProtocolError("Reply payload is truncated or oversized")
    return CommandReply(status, command, length, checksum, total, offset, report[18:18 + length])


def parse_identity_reply(
    report: bytes, *, usb_pid: int, verified_pid: int | None = None,
) -> DeviceIdentity:
    if usb_pid not in IDENTIFY_PIDS:
        raise UnsupportedProtocol(f"No identity query is validated for USB PID {usb_pid:04x}")
    if len(report) < 6 or len(report) > REPORT_SIZE or report[:2] != b"\x02\x22":
        raise ProtocolError("Malformed identity response")
    version, beta = struct.unpack_from("<HH", report, 2)
    if usb_pid == 0x3109:
        # Live 3109 data has model-specific flags and opaque trailing fields.
        # The legacy web parser's offset is not a verified firmware target here.
        return DeviceIdentity(usb_pid, version, beta, target_verified=False)
    if verified_pid not in MODERN_PIDS:
        raise ProtocolError("A separate product-ID reply is required for this device")
    product_id = verified_pid
    if usb_pid in MODERN_PIDS and product_id != usb_pid:
        raise FirmwareMismatch("Identity response does not match the selected USB target")
    # Trailing words are model-specific selectors, not proven hardware revisions.
    return DeviceIdentity(product_id, version, beta)


def _receive_matching(
    transport: ReportTransport, prefix: bytes, timeout: float,
) -> bytes:
    if not 0 < timeout <= 60:
        raise ProtocolError("Timeout must be between zero and 60 seconds")
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProtocolTimeout("Device did not return the expected report")
        try:
            report = transport.receive(remaining)
        except TimeoutError as error:
            raise ProtocolTimeout("Device did not return the expected report") from error
        if not report:
            raise ProtocolTimeout("Device did not return the expected report")
        if report.startswith(prefix):
            return report


def query_identity(
    transport: ReportTransport, *, usb_pid: int, timeout: float = 1.0,
) -> DeviceIdentity:
    if usb_pid not in IDENTIFY_PIDS:
        raise UnsupportedProtocol(f"No identity query is validated for USB PID {usb_pid:04x}")
    if not 0 < timeout <= 60:
        raise ProtocolError("Timeout must be between zero and 60 seconds")
    verified_pid = None
    if usb_pid != 0x3109:
        reply = _exchange(transport, 8, length=4, total_length=4, timeout=timeout)
        if len(reply.payload) not in (2, 4):
            raise ProtocolError("Malformed product-ID response")
        verified_pid = int.from_bytes(reply.payload, "little")
    transport.send(IDENTIFY_REPORT)
    reply = _receive_matching(transport, b"\x02\x22", timeout)
    return parse_identity_reply(reply, usb_pid=usb_pid, verified_pid=verified_pid)


def query_paired_identity(
    transport: ReportTransport, *, receiver_pid: int = 0x6013, timeout: float = 1.0,
) -> PairedControllerInfo:
    """Read controller status through an Ultimate 2 receiver, without switching modes.

The response has no firmware product ID and cannot qualify a flash target.
A timeout means no status reply was received, not proof of a disconnected pad.
    """
    if receiver_pid != 0x6013:
        raise UnsupportedProtocol("Paired-controller queries support only the Ultimate 2 receiver")
    if not 0 < timeout <= 60:
        raise ProtocolError("Timeout must be between zero and 60 seconds")
    request = b"\x81\x04" + HEADER.pack(0x0121, 0, 6, 0, 0, 0)
    transport.send(request.ljust(REPORT_SIZE, b"\0"))
    response = _receive_matching(transport, b"\x02\x04", timeout)
    if not 24 <= len(response) <= REPORT_SIZE:
        raise ProtocolError("Paired-controller response is truncated or oversized")
    status, command, length, _, _, _ = HEADER.unpack_from(response, 2)
    if status != 4 or command != 0x0121 or not 6 <= length <= PAYLOAD_SIZE or len(response) < 18 + length:
        raise ProtocolError("Paired-controller response does not match the request")
    battery = response[18]
    if battery > 100 and battery != 255:
        raise ProtocolError("Paired-controller battery value is invalid")
    version = struct.unpack_from("<H", response, 19)[0]
    return PairedControllerInfo(version, response[21], None if battery == 255 else battery, response[23])


def parse_firmware(
    data: bytes,
    *,
    expected_pid: int | None = None,
    expected_revision: int | None = None,
    expected_version: int | None = None,
) -> FirmwareImage:
    if not FIRMWARE_HEADER.size < len(data) <= MAX_IMAGE_SIZE:
        raise ProtocolError("Firmware file size is invalid")
    version, address, length, pid, reserved1, revision, reserved2 = FIRMWARE_HEADER.unpack_from(data)
    if not 0 < length <= MAX_IMAGE_SIZE - FIRMWARE_HEADER.size or len(data) != length + FIRMWARE_HEADER.size:
        raise ProtocolError("Firmware payload length does not match its header")
    if not 0 < pid < 0xFFFF or address + length > 0x100000000:
        raise ProtocolError("Firmware target or address range is invalid")
    if expected_pid is not None and pid != expected_pid:
        raise FirmwareMismatch("Firmware product ID does not match the target")
    if expected_revision is not None and revision != expected_revision:
        raise FirmwareMismatch("Firmware revision does not match the target")
    if expected_version is not None and version != expected_version:
        raise FirmwareMismatch("Firmware version does not match the selected release")
    return FirmwareImage(
        version, address, pid, reserved1, revision, reserved2,
        bytes(data[FIRMWARE_HEADER.size:]), hashlib.sha256(data).hexdigest(),
    )


def _exchange(
    transport: ReportTransport, command: int, *, timeout: float = 2.0, **fields: object,
) -> CommandReply:
    report = build_command(command, **fields)
    transport.send(report)
    reply = parse_command_reply(_receive_matching(transport, b"\x02\x05", timeout))
    if reply.command != command:
        raise ProtocolError("Device acknowledged a different command")
    if reply.status != 0:
        raise ProtocolError(f"Device rejected command {command:04x} with status {reply.status:04x}")
    return reply


def _verify_block(transport: ReportTransport, offset: int, block: bytes) -> None:
    request = struct.pack("<III", offset, len(block), BLOCK_SIZE)
    reply = _exchange(transport, 0xC3, data=request, total_length=len(request), offset=offset)
    if len(reply.payload) != 2 or struct.unpack("<H", reply.payload)[0] != crc16(block):
        raise ProtocolError("Device firmware CRC verification failed")
    for position in range(0, len(block), PAYLOAD_SIZE):
        expected = block[position:position + PAYLOAD_SIZE]
        reply = _exchange(
            transport, 5, length=len(expected), total_length=len(expected), offset=offset + position,
        )
        if reply.offset != offset + position or reply.payload != expected:
            raise ProtocolError("Device firmware readback verification failed")


def flash_application(
    transport: ReportTransport,
    image: FirmwareImage,
    *,
    identity: DeviceIdentity,
    qualification: FlashQualification,
    progress: Callable[[str, int, int], None] | None = None,
) -> FlashResult:
    """Write a qualified Ultimate 2 target in application mode.

No destructive command is retried. A failure propagates without resetting or
committing the device. The caller must reconnect and verify the version after
the returned result; this function never declares a completed update.
    """
    if identity.product_id not in MODERN_PIDS or not identity.target_verified:
        raise UnsupportedProtocol("Native flashing supports only qualified Ultimate 2 application-mode targets")
    if (
        image.product_id != identity.product_id
        or qualification.product_id != identity.product_id
        or qualification.hardware_revision != identity.hardware_revision
        or qualification.image_revision != image.revision
        or qualification.image_sha256 != image.sha256
        or not qualification.evidence.strip()
        or identity.version_raw not in qualification.source_versions
        or identity.beta != 0
    ):
        raise FirmwareMismatch("Firmware, hardware, and qualification record do not match")
    if image.address != 0x5000 or len(image.payload) > 0x3B000 or image.payload[8:12] != b"KNLT":
        raise UnsupportedProtocol("Firmware is not the qualified Ultimate 2 Telink image layout")
    encoded_image = FIRMWARE_HEADER.pack(
        image.version_raw, image.address, len(image.payload), image.product_id,
        image.reserved1, image.revision, image.reserved2,
    ) + image.payload
    if hashlib.sha256(encoded_image).hexdigest() != qualification.image_sha256:
        raise FirmwareMismatch("Firmware changed after qualification")
    current = query_identity(transport, usb_pid=identity.product_id)
    if current != identity:
        raise FirmwareMismatch("Device identity changed before updating")
    total = len(image.payload)
    notify = progress or (lambda stage, done, size: None)
    notify("preparing", 0, total)
    for block_start in range(0, total, BLOCK_SIZE):
        block = image.payload[block_start:block_start + BLOCK_SIZE]
        notify("erasing", block_start, total)
        reply = _exchange(transport, 4, total_length=len(block), offset=block_start, timeout=10.0)
        if reply.offset != block_start:
            raise ProtocolError("Erase acknowledgment address does not match")
        for position in range(0, len(block), PAYLOAD_SIZE):
            chunk = block[position:position + PAYLOAD_SIZE]
            offset = block_start + position
            reply = _exchange(transport, 3, data=chunk, total_length=total, offset=offset)
            if reply.offset != offset:
                raise ProtocolError("Write acknowledgment address does not match")
            notify("writing", offset + len(chunk), total)
        notify("verifying", block_start, total)
        _verify_block(transport, block_start, block)
    notify("committing", total, total)
    _exchange(transport, 0xC4, data=struct.pack("<II", total, image.version_raw))
    notify("restarting", total, total)
    transport.send(build_command(7))
    return FlashResult(image.product_id, image.version, total)
