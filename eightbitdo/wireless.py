"""Read-only Ultimate 2 radio-updater probe; never initializes an update."""

from dataclasses import dataclass

from .protocol import (
    DeviceIdentity, FirmwareMismatch, PairedControllerInfo, ProtocolError,
    ReportTransport, UnsupportedProtocol, _receive_matching, build_command,
    parse_command_reply, query_identity, query_paired_identity,
)


@dataclass(frozen=True)
class WirelessTarget:
    receiver: DeviceIdentity
    controller: DeviceIdentity
    status: PairedControllerInfo


def query_wireless_target(
    transport: ReportTransport, *, usb_pid: int, timeout: float = 2.0,
) -> WirelessTarget:
    """Identify a controller over the source-reviewed radio update channel.

    The inspected pair is receiver 1.06 / controller 1.10, both stable.
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
    if status.version_raw != 110 or status.beta:
        raise UnsupportedProtocol("Radio updater probes are reviewed only for stable controller firmware 1.10")

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
