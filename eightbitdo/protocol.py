"""8BitDo application-mode HID protocol; see docs/protocol.md for evidence.

HID reports include their report ID. The application must qualify hardware and
own the device lock and suspend inhibitor before calling flash_application.
"""

from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


REPORT_SIZE = 64
PAYLOAD_SIZE = 46
BLOCK_SIZE = 4096
MAX_IMAGE_SIZE = 8 * 1024 * 1024
MODERN_PIDS = frozenset({0x6012, 0x6013})
STAGING_BASES = {0x6012: 0x32000, 0x6013: 0x13000}
METADATA_BASES = {0x6012: 0x31000, 0x6013: 0x12000}
# Keep the rounded installed image below metadata and the staging image within writable flash.
MAX_APPLICATION_SIZES = {0x6012: 0x2C000, 0x6013: 0xC000}
# Desktop updater model tags, not USB PIDs; see docs/protocol.md.
LEGACY_TARGETS = {
    0x3109: {0x0023: 0x6008, 0x3013: 0x3013, 0x9023: 0x3013},
    0x6007: {0x0022: 0x6007, 0x0023: 0x6008},
}
IDENTIFY_PIDS = frozenset({*LEGACY_TARGETS, 0x310B, *MODERN_PIDS})
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
class FlashAuthorization:
    """Common developer-issued binding; the base type alone cannot authorize a write."""

    product_id: int
    hardware_revision: int | None
    image_revision: int
    image_sha256: str
    evidence: str
    source_versions: tuple[int, ...] = ()


@dataclass(frozen=True)
class FlashQualification(FlashAuthorization):
    """A hardware-validated update record, never constructed from UI arguments."""

    evidence_level: str = field(default="hardware-validated", init=False)


@dataclass(frozen=True)
class SourceRegion:
    address: int
    length: int
    sha256: str


@dataclass(frozen=True)
class SourceReviewedAuthorization(FlashAuthorization):
    """Experimental source review, explicitly distinct from a successful hardware update."""

    source_regions: tuple[SourceRegion, ...] = ()
    evidence_level: str = field(default="source-reviewed-experimental", init=False)


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
    if length > PAYLOAD_SIZE:
        raise ProtocolError("Reply payload is oversized")
    # These handlers echo the request length, independently of the response data size.
    payload_length = length
    if status == 0:
        if command in (0xC1, 0xC3):
            declared, payload_length = {0xC1: (0, 16), 0xC3: (12, 6)}[command]
            if length != declared:
                raise ProtocolError("Reply length does not match the command format")
        elif command in (3, 4, 7, 0xC4):
            payload_length = 0
    if len(report) < 18 + payload_length:
        raise ProtocolError("Reply payload is truncated or oversized")
    return CommandReply(status, command, length, checksum, total, offset, report[18:18 + payload_length])


def parse_identity_reply(
    report: bytes, *, usb_pid: int, verified_pid: int | None = None,
) -> DeviceIdentity:
    if usb_pid not in IDENTIFY_PIDS:
        raise UnsupportedProtocol(f"No identity query is validated for USB PID {usb_pid:04x}")
    if len(report) < 6 or len(report) > REPORT_SIZE or report[:2] != b"\x02\x22":
        raise ProtocolError("Malformed identity response")
    version, beta = struct.unpack_from("<HH", report, 2)
    if usb_pid in LEGACY_TARGETS:
        if len(report) < 8:
            raise ProtocolError("Legacy identity response is missing its model tag")
        tag = int.from_bytes(report[6:8], "little")
        target = LEGACY_TARGETS[usb_pid].get(tag)
        # Byte 5 contains flags. Unknown tags must not use the vendor's fallback.
        return DeviceIdentity(target or usb_pid, version, report[4], target_verified=target is not None)
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
    if usb_pid not in LEGACY_TARGETS:
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
    _, _, length, checksum, total, offset = HEADER.unpack_from(report, 2)
    if command == 8:
        if reply.length not in (2, 4) or reply.total_length != reply.length:
            raise ProtocolError("Malformed product-ID response")
        length, total = reply.length, reply.total_length
    if (reply.length, reply.crc, reply.total_length, reply.offset) != (length, checksum, total, offset):
        raise ProtocolError("Device acknowledgment header does not match the request")
    return reply


def _verify_block(transport: ReportTransport, offset: int, block: bytes, *, staging_base: int) -> None:
    request = struct.pack("<III", offset, len(block), BLOCK_SIZE)
    reply = _exchange(transport, 0xC3, data=request, total_length=len(request), offset=offset)
    # Firmware checks complete 128-byte units; exact readback below also covers the remaining tail.
    covered = block[:len(block) // 128 * 128]
    device_crc, device_sum = struct.unpack("<HI", reply.payload)
    if device_crc != crc16(covered):
        raise ProtocolError("Device firmware CRC verification failed")
    if device_sum != sum(covered):
        raise ProtocolError("Device firmware additive checksum verification failed")
    for position in range(0, len(block), PAYLOAD_SIZE):
        expected = block[position:position + PAYLOAD_SIZE]
        # Write/erase/CRC commands add the staging base; command 05 reads absolute flash addresses.
        read_address = staging_base + offset + position
        reply = _exchange(
            transport, 5, length=len(expected), total_length=len(expected), offset=read_address,
        )
        if reply.offset != read_address or reply.payload != expected:
            raise ProtocolError("Device firmware readback verification failed")


def _check_commit_metadata(transport: ReportTransport, product_id: int) -> None:
    record = bytearray()
    for offset, count in ((0, 32), (32, 20)):
        reply = _exchange(
            transport, 5, length=count, total_length=count,
            offset=METADATA_BASES[product_id] + offset,
        )
        record.extend(reply.payload)
    # Factory receivers can have an erased record; the bootloader ignores its invalid CRC.
    if record == b"\xff" * 52:
        return
    if crc16(record[:48]) != struct.unpack_from("<I", record, 48)[0]:
        raise ProtocolError("Commit metadata is damaged; recovery review is required before updating")
    if struct.unpack_from("<I", record, 24)[0] == 0xAEB14D6C:
        raise ProtocolError("A firmware image is already pending; do not overwrite its staging area")


def _validate_flash_authorization(
    image: FirmwareImage, identity: DeviceIdentity, qualification: FlashAuthorization,
) -> tuple[SourceRegion, ...]:
    if identity.product_id not in MODERN_PIDS or not identity.target_verified:
        raise UnsupportedProtocol("Native flashing supports only qualified Ultimate 2 application-mode targets")
    if type(qualification) not in (FlashQualification, SourceReviewedAuthorization):
        raise UnsupportedProtocol("A developer-issued hardware or source-reviewed authorization is required")
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
    if image.address != 0x5000 or len(image.payload) < 32 or image.payload[8:12] != b"KNLT":
        raise UnsupportedProtocol("Firmware is not the qualified Ultimate 2 Telink image layout")
    rounded_size = (len(image.payload) + BLOCK_SIZE - 1) // BLOCK_SIZE * BLOCK_SIZE
    if rounded_size > MAX_APPLICATION_SIZES[identity.product_id]:
        raise UnsupportedProtocol("Firmware exceeds the model's installed or staging flash bounds")
    if int.from_bytes(image.payload[28:30], "little") != identity.product_id:
        raise FirmwareMismatch("Embedded firmware product ID does not match the target")
    encoded_image = FIRMWARE_HEADER.pack(
        image.version_raw, image.address, len(image.payload), image.product_id,
        image.reserved1, image.revision, image.reserved2,
    ) + image.payload
    if hashlib.sha256(encoded_image).hexdigest() != qualification.image_sha256:
        raise FirmwareMismatch("Firmware changed after qualification")
    if isinstance(qualification, FlashQualification):
        return ()
    return _validate_source_regions(qualification.source_regions, identity.product_id)


def _validate_source_regions(regions: tuple[SourceRegion, ...], product_id: int) -> tuple[SourceRegion, ...]:
    if type(regions) is not tuple or len(regions) != 2 or any(type(r) is not SourceRegion for r in regions):
        raise UnsupportedProtocol("Source review requires immutable boot and installed application fingerprints")
    for region in regions:
        if (
            type(region.address) is not int or type(region.length) is not int
            or not isinstance(region.sha256, str) or len(region.sha256) != 64
            or any(c not in "0123456789abcdef" for c in region.sha256)
        ):
            raise UnsupportedProtocol("Invalid source fingerprint")
    by_address = {region.address: region for region in regions}
    if (
        set(by_address) != {0, 0x5000} or by_address[0].length != 0x5000
        or not 36 <= by_address[0x5000].length <= MAX_APPLICATION_SIZES[product_id]
    ):
        raise UnsupportedProtocol("Source fingerprints must cover the bootloader and bounded installed application")
    return (by_address[0], by_address[0x5000])


def verify_authorization_sources(
    transport: ReportTransport,
    image: FirmwareImage,
    *,
    identity: DeviceIdentity,
    qualification: FlashAuthorization,
) -> None:
    """Validate the authorization, then read and hash experimental source regions.

    Preparation and installation must both call this check. It never initializes,
    writes, or resets a device, and never returns the source bytes to the caller.
    """
    _verify_source_regions(transport, _validate_flash_authorization(image, identity, qualification), identity.product_id)


def _verify_source_regions(transport: ReportTransport, regions: tuple[SourceRegion, ...], product_id: int) -> None:
    for region in regions:
        digest = hashlib.sha256()
        first = b""
        for position in range(0, region.length, 32):
            count = min(32, region.length - position)
            data = _exchange(
                transport, 5, length=count, total_length=count, offset=region.address + position,
            ).payload
            digest.update(data)
            if position == 0:
                first = data
        if digest.hexdigest() != region.sha256:
            raise FirmwareMismatch("Installed firmware or bootloader differs from the source-reviewed fingerprint")
        if region.address == 0x5000:
            extent = int.from_bytes(first[24:28], "little")
            if (
                first[8:12] != b"KNLT" or int.from_bytes(first[28:30], "little") != product_id
                or not 32 <= extent <= region.length - 4 or extent % 4
            ):
                raise FirmwareMismatch("Source fingerprint does not cover a complete matching application")


def flash_application(
    transport: ReportTransport,
    image: FirmwareImage,
    *,
    identity: DeviceIdentity,
    qualification: FlashAuthorization,
    progress: Callable[[str, int, int], None] | None = None,
) -> FlashResult:
    """Write an authorized Ultimate 2 target in application mode.

No destructive command is retried. A failure propagates without resetting or
committing the device. The caller must reconnect and verify the version after
the returned result; this function never declares a completed update.
    """
    _validate_flash_authorization(image, identity, qualification)
    current = query_identity(transport, usb_pid=identity.product_id)
    if current != identity:
        raise FirmwareMismatch("Device identity changed before updating")
    _check_commit_metadata(transport, identity.product_id)
    verify_authorization_sources(transport, image, identity=identity, qualification=qualification)
    total = len(image.payload)
    notify = progress or (lambda stage, done, size: None)
    notify("preparing", 0, total)
    initialization = _exchange(transport, 0xC1)
    initial_address = struct.unpack_from("<I", initialization.payload)[0]
    block_size = struct.unpack_from("<I", initialization.payload, 12)[0]
    if initial_address != 0 or block_size != BLOCK_SIZE:
        raise ProtocolError("Device initialization returned an unsupported flash block size")
    return _flash_application_blocks(transport, image, progress=progress)


def _flash_application_blocks(
    transport: ReportTransport, image: FirmwareImage,
    *, progress: Callable[[str, int, int], None] | None = None,
) -> FlashResult:
    """Transfer after the route has verified identity, sources, metadata, and initialization."""
    total = len(image.payload)
    notify = progress or (lambda stage, done, size: None)
    for block_start in range(0, total, BLOCK_SIZE):
        block = image.payload[block_start:block_start + BLOCK_SIZE]
        notify("erasing", block_start, total)
        reply = _exchange(transport, 4, offset=block_start, timeout=10.0)
        if reply.offset != block_start:
            raise ProtocolError("Erase acknowledgment address does not match")
        for position in range(0, len(block), PAYLOAD_SIZE):
            chunk = block[position:position + PAYLOAD_SIZE]
            offset = block_start + position
            reply = _exchange(transport, 3, data=chunk, total_length=len(chunk), offset=offset)
            if reply.offset != offset:
                raise ProtocolError("Write acknowledgment address does not match")
            notify("writing", offset + len(chunk), total)
        notify("verifying", block_start, total)
        _verify_block(transport, block_start, block, staging_base=STAGING_BASES[image.product_id])
    # Recheck earlier blocks after all writes, before making the staged image bootable.
    for block_start in range(0, total, BLOCK_SIZE):
        notify("verifying", block_start, total)
        _verify_block(
            # The bootloader copies whole sectors, including the final sector's
            # erased tail. Those bytes must be verified before commit as well.
            transport, block_start, image.payload[block_start:block_start + BLOCK_SIZE].ljust(BLOCK_SIZE, b"\xff"),
            staging_base=STAGING_BASES[image.product_id],
        )
    notify("committing", total, total)
    _exchange(transport, 0xC4, data=struct.pack("<II", total, image.version_raw), total_length=8)
    notify("restarting", total, total)
    transport.send(build_command(7))
    return FlashResult(image.product_id, image.version, total)
