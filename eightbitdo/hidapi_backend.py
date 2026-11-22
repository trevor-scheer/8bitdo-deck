"""Application-mode HID discovery and read-only queries for macOS and Windows.

Opaque HID paths identify an interface for this connection only. They are never
treated as physical USB-port evidence for reconnecting a bootloader.
"""

from contextlib import ExitStack
import hashlib
import math
from pathlib import Path

from .discovery import Device, clean_text, inspect_device, report_layout
from .locking import exclusive_lock
from .protocol import IDENTIFY_REPORT, HEADER, build_command
from .registry import BY_USB_PID, QUERY_PIDS


CONTROL_PAGES = {0xFFA0, 0xFF7A, 0x008C}
READ_ONLY_REPORTS = frozenset({
    IDENTIFY_REPORT,
    build_command(8, length=4, total_length=4),
    (b"\x81\x04" + HEADER.pack(0x0121, 0, 6, 0, 0, 0)).ljust(64, b"\0"),
})
WRITE_UNAVAILABLE = (
    "Firmware installation and recovery are not enabled on this operating system: "
    "physical-device reconnect and recovery have not been validated."
)


def _hid():
    try:
        import hid
    except ImportError:
        raise RuntimeError("HIDAPI is unavailable. Reinstall the desktop application or its Python dependencies.") from None
    return hid


def _is_usb_control(row: dict) -> bool:
    return (row.get("vendor_id") == 0x2DC8 and row.get("product_id") in QUERY_PIDS
            and row.get("bus_type") == 1 and row.get("usage_page") in CONTROL_PAGES)


def discover_hidapi(*, hid=None) -> list[Device]:
    hid = hid or _hid()
    devices = {}
    for row in hid.enumerate(0x2DC8, 0):
        path, pid = row.get("path"), row.get("product_id")
        if row.get("vendor_id") != 0x2DC8 or not isinstance(path, bytes) or not path or b"\0" in path:
            continue
        if type(pid) is not int or not 0 <= pid <= 0xFFFF:
            continue
        # HIDAPI can list several collections for one path. Keep the vendor
        # collection when present; never merge separate paths by serial number.
        queryable = _is_usb_control(row)
        if path in devices and (devices[path].hid_paths or not queryable):
            continue
        model = BY_USB_PID.get(pid)
        device = Device(
            "usb-" + hashlib.sha256(path).hexdigest()[:16],
            clean_text(str(row.get("product_string") or "8BitDo device")),
            "2dc8", f"{pid:04x}", role=model.role if model else "unknown",
            model_id=model.id if model else None, backend="hidapi",
            hid_paths=[path] if queryable else [],
            reason=("USB control interface detected. " if queryable else
                    "Connect the device over USB or its receiver for a supported identity query. ") + WRITE_UNAVAILABLE,
        )
        devices[path] = device
    return sorted(devices.values(), key=lambda device: device.id)


def inspect_hidapi(device: Device, lock_dir: Path) -> Device:
    result = inspect_device(device, lock_dir, transport_factory=HidapiTransport, check_permissions=False)
    result.can_update = False
    if result.status == "identified":
        result.reason = "Firmware identity read successfully. " + WRITE_UNAVAILABLE
    return result


class HidapiTransport:
    """Report-validated transport which rejects every non-query output packet."""

    def __init__(self, path: bytes, pid: int, lock_path: Path, *, expected_usb_path=None, hid=None):
        if not isinstance(path, bytes) or not path or b"\0" in path or pid not in QUERY_PIDS:
            raise ValueError("A known application-mode USB HID interface is required.")
        if expected_usb_path:
            raise ValueError("HIDAPI paths cannot establish physical bootloader correlation.")
        self.path, self.pid, self.lock_path = path, pid, lock_path
        self.hid = hid
        self.device = None
        self._stack = None

    def _validate_enumeration(self):
        rows = (self.hid.enumerate(0x2DC8, self.pid))
        if not any(row.get("path") == self.path and row.get("product_id") == self.pid
                   and _is_usb_control(row) for row in rows):
            raise ValueError("The USB control interface changed. Scan again.")

    def __enter__(self):
        self._stack = ExitStack()
        try:
            self._stack.enter_context(exclusive_lock(self.lock_path))
            self.hid = self.hid or _hid()
            self._validate_enumeration()
            self.device = self.hid.device()
            self._stack.callback(self.device.close)
            self.device.open_path(self.path)
            descriptor = bytes(self.device.get_report_descriptor())
            if not 0 < len(descriptor) <= 4096:
                raise ValueError("Invalid HID report descriptor size.")
            pages, inputs, outputs = report_layout(descriptor)
            if not (pages & CONTROL_PAGES and 0x02 in inputs and 0x81 in outputs):
                raise ValueError("The opened HID interface does not match the control protocol.")
            self._validate_enumeration()
            if self.device.set_nonblocking(1) != 0:
                raise OSError("Could not configure bounded HID reads.")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self._stack is not None:
                self._stack.close()
        finally:
            self._stack = None
            self.device = None

    def send(self, report: bytes) -> None:
        if self.device is None:
            raise RuntimeError("The HID connection is closed.")
        if report not in READ_ONLY_REPORTS:
            raise ValueError("This platform permits only reviewed read-only identity queries.")
        for _ in range(128):
            if not self.device.read(65):
                break
        else:
            raise RuntimeError("The device input queue is busy. Close other controller software and retry.")
        if self.device.write(report) != len(report):
            raise OSError("Incomplete HID request; operation stopped.")

    def receive(self, timeout: float) -> bytes:
        if self.device is None:
            raise RuntimeError("The HID connection is closed.")
        if not 0 < timeout <= 60:
            raise ValueError("HID timeout must be between zero and 60 seconds.")
        result = bytes(self.device.read(65, max(1, math.ceil(timeout * 1000))))
        if not result:
            raise TimeoutError("The device did not reply. Check its USB connection.")
        return result
