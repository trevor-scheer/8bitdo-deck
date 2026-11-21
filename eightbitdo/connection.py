"""Bounded receiver reconnection on the original physical USB port."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import time
from typing import Iterator

from .access import recovery_access_configured
from .discovery import Device, discover
from .protocol import DeviceIdentity, FirmwareMismatch, ProtocolError, query_identity
from .registry import QUERY_PIDS
from .transport import BootHidrawTransport, HidrawTransport


BOOT_PIDS = frozenset({0x3208, 0x3002})


@dataclass(frozen=True)
class BootConnection:
    transport: BootHidrawTransport
    usb_pid: int


def _original_port(original: Device) -> Path:
    port = Path(original.usb_path)
    if original.backend != "hid" or original.vid.lower() != "2dc8" or not original.usb_path or not port.is_absolute():
        raise ValueError("The original physical 8BitDo USB port is required.")
    # Discovery already resolved this path. Resolving it again while absent can
    # silently follow a changed symlink instead of retaining the original port.
    if str(port) != os.path.normpath(str(port)) or ".." in port.parts:
        raise ValueError("The original USB port must be a canonical path.")
    return port


def _lock_path(original: Device, lock_dir: Path) -> Path:
    digest = hashlib.sha256(str(_original_port(original)).encode()).hexdigest()[:16]
    return lock_dir / f"usb-{digest}.lock"


def require_boot_access(original: Device) -> None:
    """Call before requesting any mode change; this never installs permission rules.

    The root-owned setup receipt establishes that the scoped boot rules were
    installed and reloaded. Actual boot-node access is checked again on open.
    """
    _original_port(original)
    if not recovery_access_configured():
        raise PermissionError("Configure recovery USB access before entering update mode.")
    if not original.hid_paths or not any(os.access(path, os.R_OK | os.W_OK) for path in original.hid_paths):
        raise PermissionError("The application USB control interface is not accessible.")


def _boot_interfaces(original: Device, sys_root: Path, dev_root: Path) -> list[tuple[Path, int]]:
    port = _original_port(original)
    found = []
    for hid in sorted((sys_root / "class/hidraw").glob("hidraw*")):
        try:
            interface = (hid / "device").resolve(strict=True)
            parent = next((p for p in interface.parents if (p / "idVendor").is_file()), None)
            if parent != port or (parent / "idVendor").read_text().strip().lower() != "2dc8":
                continue
            pid = int((parent / "idProduct").read_text().strip(), 16)
            if pid in BOOT_PIDS:
                found.append((dev_root / hid.name, pid))
        except (OSError, ValueError):
            continue
    return found


def _deadline(timeout: float) -> float:
    if not 0 < timeout <= 60:
        raise ValueError("Reconnection timeout must be between zero and 60 seconds.")
    return time.monotonic() + timeout


def _pause(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(0.1, remaining))


@contextmanager
def wait_for_boot(
    original: Device,
    lock_dir: Path,
    *,
    timeout: float = 15.0,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
) -> Iterator[BootConnection]:
    """Open the same port's boot interface; caller must verify its firmware target.

    This performs no device commands. The open fd is rechecked by the transport
    for physical port, USB identity, and unnumbered 64-byte report descriptor.
    Call require_boot_access before the separate application-to-boot command.
    """
    port = _original_port(original)
    deadline = _deadline(timeout)
    if not recovery_access_configured():
        raise PermissionError("Recovery USB access is not configured.")
    last_error = None
    transport = None
    boot_pid = None
    while time.monotonic() < deadline:
        for path, pid in _boot_interfaces(original, sys_root, dev_root):
            candidate = BootHidrawTransport(path, pid, _lock_path(original, lock_dir), expected_usb_path=str(port))
            try:
                candidate.__enter__()
            except (OSError, ValueError, RuntimeError) as error:
                last_error = error
                continue
            transport, boot_pid = candidate, pid
            break
        if transport is not None:
            break
        _pause(deadline)
    if transport is None:
        if isinstance(last_error, PermissionError):
            raise PermissionError("The receiver entered update mode, but its boot USB interface is inaccessible.") from last_error
        raise TimeoutError("The original USB port did not expose a usable receiver boot interface.") from last_error
    try:
        yield BootConnection(transport, boot_pid)
    finally:
        transport.__exit__(None, None, None)


def wait_for_application(
    original: Device,
    lock_dir: Path,
    *,
    expected_product_id: int,
    expected_version_raw: int,
    timeout: float = 30.0,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
) -> tuple[Device, DeviceIdentity]:
    """Verify the same port's runtime identity/version after reset, using reads only."""
    port = _original_port(original)
    deadline = _deadline(timeout)
    last_error = None
    while time.monotonic() < deadline:
        devices = [device for device in discover(sys_root, dev_root) if device.usb_path == str(port)]
        for device in devices:
            usb_pid = int(device.pid, 16)
            if usb_pid not in QUERY_PIDS:
                continue
            for path in device.hid_paths:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    with HidrawTransport(path, usb_pid, _lock_path(original, lock_dir), expected_usb_path=str(port)) as transport:
                        # The identity helper can perform two exchanges.
                        identity = query_identity(transport, usb_pid=usb_pid, timeout=min(1.0, remaining / 2))
                except (OSError, ValueError, RuntimeError) as error:
                    last_error = error
                    continue
                if not identity.target_verified or identity.product_id != expected_product_id:
                    raise FirmwareMismatch("The returning USB device does not report the expected firmware target.")
                if identity.version_raw != expected_version_raw or identity.beta != 0:
                    last_error = ProtocolError("The returning receiver does not report the installed firmware version.")
                    continue
                device.firmware_pid = identity.product_id
                device.firmware_version = identity.version
                device.hardware_revision = identity.hardware_revision
                return device, identity
        _pause(deadline)
    raise TimeoutError("The original receiver's expected firmware version could not be verified after reset.") from last_error
