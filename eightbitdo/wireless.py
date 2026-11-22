"""Source-bound Ultimate 2 firmware transfer through its 2.4 GHz USB receiver."""

from dataclasses import dataclass
from typing import Callable

from .protocol import (
    HEADER, REPORT_SIZE, DeviceIdentity, FirmwareImage, FirmwareMismatch, FlashResult,
    PairedControllerInfo, ProtocolError, SourceRegion, SourceReviewedAuthorization,
    ReportTransport, UnsupportedProtocol, _receive_matching, build_command,
    parse_command_reply, query_identity, query_paired_identity,
    _check_commit_metadata, _flash_application_blocks, _validate_flash_authorization,
    _validate_source_regions, _verify_source_regions, verify_authorization_sources,
)


@dataclass(frozen=True)
class WirelessTarget:
    receiver: DeviceIdentity
    controller: DeviceIdentity
    status: PairedControllerInfo


@dataclass(frozen=True)
class WirelessAuthorization:
    controller: SourceReviewedAuthorization
    receiver_version_raw: int
    receiver_source_regions: tuple[SourceRegion, ...]
    controller_edition: int
    minimum_battery_percent: int = 30


class WirelessTransport:
    """Forward reviewed updater packets; never translate ordinary USB identity reports.

    The caller owns the receiver's physical-device lock for the entire operation.
    Read-only instances cannot initialize, checksum, erase, write, commit, or reset.
    """

    def __init__(self, transport: ReportTransport, *, allow_update: bool = False):
        self.transport = transport
        self.allow_update = allow_update

    def send(self, report: bytes) -> None:
        if len(report) != REPORT_SIZE or report[:2] != b"\x81\x05":
            raise UnsupportedProtocol("Wireless updates require application updater framing")
        command, _, length, _, _, offset = HEADER.unpack_from(report, 2)
        allowed = {5, 8} | ({0xC1, 4, 3, 0xC3, 0xC4, 7} if self.allow_update else set())
        if command not in allowed:
            raise UnsupportedProtocol("Command is not permitted on this wireless transport")
        if command == 5 and (not 0 < length <= 46 or offset + length > 0x5E000):
            raise UnsupportedProtocol("Wireless read is outside the reviewed controller flash bounds")
        self.transport.send(report[:1] + b"\x06" + report[2:])

    def receive(self, timeout: float) -> bytes:
        return self.transport.receive(timeout)


def _validate_wireless_authorization(image: FirmwareImage, authorization: WirelessAuthorization) -> None:
    if type(authorization) is not WirelessAuthorization or type(authorization.controller) is not SourceReviewedAuthorization:
        raise UnsupportedProtocol("Wireless updates require a source-reviewed receiver and controller authorization")
    controller = authorization.controller
    if (controller.product_id != 0x6012 or not controller.source_versions
            or authorization.receiver_version_raw != 106
            or type(authorization.controller_edition) is not int or not 0 <= authorization.controller_edition <= 255
            or type(authorization.minimum_battery_percent) is not int
            or not 30 <= authorization.minimum_battery_percent <= 100):
        raise UnsupportedProtocol("Wireless authorization is outside the reviewed route")
    _validate_flash_authorization(
        image, DeviceIdentity(0x6012, controller.source_versions[0], hardware_revision=controller.hardware_revision), controller,
    )
    _validate_source_regions(authorization.receiver_source_regions, 0x6013)


def _require_wireless_target(target: WirelessTarget, authorization: WirelessAuthorization) -> None:
    if (target.receiver.pid != 0x6013 or target.receiver.version_raw != authorization.receiver_version_raw
            or target.receiver.beta or not target.receiver.target_verified
            or target.controller.pid != 0x6012 or target.controller.version_raw not in authorization.controller.source_versions
            or target.controller.beta or not target.controller.target_verified
            or target.status.edition != authorization.controller_edition):
        raise FirmwareMismatch("Wireless receiver, controller, or edition changed or is not authorized")
    if target.status.battery_percent is None or target.status.battery_percent < authorization.minimum_battery_percent:
        raise UnsupportedProtocol(f"Charge the controller to at least {authorization.minimum_battery_percent}% before a wireless update")


def _same_target(first: WirelessTarget, second: WirelessTarget) -> bool:
    return (first.receiver, first.controller, first.status.edition) == (second.receiver, second.controller, second.status.edition)


def verify_wireless_sources(
    transport: ReportTransport, image: FirmwareImage, *, usb_pid: int, authorization: WirelessAuthorization,
) -> WirelessTarget:
    """Read-only preparation shared with the install worker; no update-state commands."""
    _validate_wireless_authorization(image, authorization)
    target = query_wireless_target(transport, usb_pid=usb_pid)
    _require_wireless_target(target, authorization)
    _verify_source_regions(transport, _validate_source_regions(authorization.receiver_source_regions, 0x6013), 0x6013)
    radio = WirelessTransport(transport)
    verify_authorization_sources(radio, image, identity=target.controller, qualification=authorization.controller)
    _check_commit_metadata(radio, target.controller.pid)
    after = query_wireless_target(transport, usb_pid=usb_pid)
    _require_wireless_target(after, authorization)
    if not _same_target(target, after):
        raise FirmwareMismatch("Wireless target changed during source verification")
    return after


def flash_wireless_controller(
    transport: ReportTransport, image: FirmwareImage, *, usb_pid: int, authorization: WirelessAuthorization,
    expected_target: WirelessTarget | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> FlashResult:
    """Stage, fully verify, commit, and reset once; the caller must verify reconnect.

    No destructive packet is retried and a failure never sends a subsequent commit
    or reset. Hold the receiver lock and prevent suspend through reconnect.
    """
    target = verify_wireless_sources(transport, image, usb_pid=usb_pid, authorization=authorization)
    if expected_target is not None and not _same_target(target, expected_target):
        raise FirmwareMismatch("Wireless target differs from the prepared controller")
    if progress:
        progress("preparing", 0, len(image.payload))
    radio = WirelessTransport(transport, allow_update=True)
    radio.send(build_command(0xC1))
    reply = _receive_matching(radio, b"\x02\x05", 2.0)
    # The RF C1 handler sends request.length + 17 bytes, so length zero carries
    # only the header. Its locally constructed USB geometry is not transmitted.
    if not 18 <= len(reply) <= REPORT_SIZE or HEADER.unpack_from(reply, 2) != (0, 0xC1, 0, 0, 0, 0):
        raise ProtocolError("Wireless initialization acknowledgment does not match the request")
    # Exact installed code fingerprints establish logical base zero and 4096-byte
    # blocks. Never interpret padded or stale RF response bytes as geometry.
    return _flash_application_blocks(radio, image, progress=progress)


def query_wireless_target(
    transport: ReportTransport, *, usb_pid: int, timeout: float = 2.0,
) -> WirelessTarget:
    """Identify a controller over the source-reviewed radio update channel.

    The reviewed route is receiver 1.06 / controller 1.09 or 1.10, all stable.
    Other versions must be reviewed before extending this read-only allowlist.
    Identity verification does not qualify an update or a recovery procedure.
    """
    if usb_pid not in (0x310B, 0x6013):
        raise UnsupportedProtocol("Wireless probes require an Ultimate 2 USB receiver")
    if not 0 < timeout <= 60:
        raise ProtocolError("Timeout must be between zero and 60 seconds")
    receiver = query_identity(transport, usb_pid=usb_pid, timeout=timeout)
    if not receiver.target_verified or receiver.pid != 0x6013:
        raise FirmwareMismatch("The connected USB device is not an Ultimate 2 receiver")
    if receiver.version_raw != 106 or receiver.beta:
        raise UnsupportedProtocol("Radio updater probes are reviewed only for stable receiver firmware 1.06")
    status = query_paired_identity(transport, receiver_pid=receiver.pid, timeout=timeout)
    if status.version_raw not in (109, 110) or status.beta:
        raise UnsupportedProtocol("Radio updater probes are reviewed only for stable controller firmware 1.09 or 1.10")

    # Class 06 forwards to the controller; the reply retains class 05 framing.
    # Command 08 only returns a PID. C1/C3 are deliberately not sent here.
    request = bytearray(build_command(8, length=4, total_length=4))
    request[1] = 0x06
    transport.send(bytes(request))
    reply = parse_command_reply(_receive_matching(transport, b"\x02\x05", timeout))
    if (reply.status, reply.command, reply.length, reply.total_length, reply.offset) != (0, 8, 2, 2, 0):
        raise ProtocolError("Radio identity reply does not match the request")
    target = int.from_bytes(reply.payload, "little")
    if target != 0x6012:
        raise FirmwareMismatch("Radio reply does not identify an Ultimate 2 controller")
    after = query_paired_identity(transport, receiver_pid=receiver.pid, timeout=timeout)
    if (after.version_raw, after.beta, after.edition) != (status.version_raw, status.beta, status.edition):
        raise FirmwareMismatch("Paired controller status changed during the probe")
    return WirelessTarget(receiver, DeviceIdentity(target, after.version_raw, after.beta), after)
