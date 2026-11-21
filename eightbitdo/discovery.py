import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from .registry import BY_USB_PID, QUERY_PIDS, model_for_identity


def clean_text(value: str, limit: int = 160) -> str:
    return "".join(c for c in value if c.isprintable())[:limit]


def report_layout(descriptor: bytes) -> tuple[set[int], set[int], set[int]]:
    """Read HID global items and report sizes without interpreting device input."""
    pages: set[int] = set()
    inputs: set[int] = set()
    outputs: set[int] = set()
    state = {"page": 0, "report": 0, "size": 0, "count": 0}
    stack: list[dict] = []
    bits: dict[tuple[int, int], int] = {}
    report_pages: dict[tuple[int, int], set[int]] = {}
    offset = 0
    while offset < len(descriptor):
        prefix = descriptor[offset]
        offset += 1
        if prefix == 0xFE:
            if offset + 2 > len(descriptor):
                raise ValueError("Truncated HID long item")
            length = descriptor[offset]
            offset += 2 + length
            if offset > len(descriptor):
                raise ValueError("Truncated HID long item")
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        if offset + size > len(descriptor):
            raise ValueError("Truncated HID item")
        value = int.from_bytes(descriptor[offset:offset + size], "little")
        offset += size
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:
            key = {0: "page", 7: "size", 8: "report", 9: "count"}.get(tag)
            if key:
                if key == "report" and not 1 <= value <= 255:
                    raise ValueError("Invalid HID report ID")
                state[key] = value
            elif tag == 10:
                stack.append(state.copy())
            elif tag == 11:
                if not stack:
                    raise ValueError("Unbalanced HID state")
                state = stack.pop()
        elif kind == 0 and tag in (8, 9):
            key = (tag, state["report"])
            bits[key] = bits.get(key, 0) + state["size"] * state["count"]
            report_pages.setdefault(key, set()).add(state["page"])
    for (tag, report), size in bits.items():
        if size == 504:
            pages.update(report_pages[(tag, report)])
            (inputs if tag == 8 else outputs).add(report)
    return pages, inputs, outputs


@dataclass
class Device:
    id: str
    name: str
    vid: str
    pid: str
    role: str = "unknown"
    model_id: str | None = None
    firmware_version: str | None = None
    status: str = "detected"
    can_update: bool = False
    reason: str = "Identify the exact model before choosing firmware."
    hid_paths: list[Path] = field(default_factory=list, repr=False)
    backend: str = "hid"
    firmware_pid: int | None = None
    hardware_revision: int | None = None
    usb_path: str = field(default="", repr=False)
    paired_firmware_version: str | None = None

    def public(self) -> dict:
        return {key: getattr(self, key) for key in (
            "id", "name", "vid", "pid", "role", "model_id", "firmware_version",
            "status", "can_update", "reason", "backend",
        )}


def discover(sys_root: Path = Path("/sys"), dev_root: Path = Path("/dev")) -> list[Device]:
    devices: dict[Path, Device] = {}
    for usb in sorted((sys_root / "bus/usb/devices").glob("*")):
        try:
            if (usb / "idVendor").read_text().strip().lower() != "2dc8":
                continue
            pid = int((usb / "idProduct").read_text().strip(), 16)
            try:
                name = clean_text((usb / "product").read_text().strip()) or "8BitDo device"
            except OSError:
                name = "8BitDo device"
            resolved = usb.resolve()
            # The ID is stable across hidraw renumbering and contains no device serial.
            device_id = "usb-" + hashlib.sha256(str(resolved).encode()).hexdigest()[:16]
            model = BY_USB_PID.get(pid)
            devices[resolved] = Device(
                device_id, name, "2dc8", f"{pid:04x}",
                role=model.role if model else "unknown",
                model_id=model.id if model else None,
                reason=model.support if model else "USB identity is ambiguous. Use model guidance; no native flash is enabled.",
                usb_path=str(resolved),
            )
        except (OSError, ValueError):
            continue
    for hid in sorted((sys_root / "class/hidraw").glob("hidraw*")):
        try:
            resolved = (hid / "device").resolve()
            parent = next((p for p in resolved.parents if p in devices), None)
            if parent is None:
                continue
            pages, inputs, outputs = report_layout((hid / "device/report_descriptor").read_bytes())
            if pages & {0xFFA0, 0xFF7A, 0x008C} and 0x02 in inputs and 0x81 in outputs:
                devices[parent].hid_paths.append(dev_root / hid.name)
        except (OSError, ValueError):
            continue
    return list(devices.values())


def inspect_device(device: Device, lock_dir: Path) -> Device:
    from .protocol import query_identity, query_paired_identity
    from .transport import HidrawTransport

    pid = int(device.pid, 16)
    if pid not in QUERY_PIDS:
        return device
    if not device.hid_paths:
        device.status = "connection_required"
        device.reason = "No compatible USB control interface. Connect by USB or follow the model's official connection instructions."
        return device
    errors = []
    for path in device.hid_paths:
        if not os.access(path, os.R_OK | os.W_OK):
            errors.append("USB access denied. Use the official updater's Linux setup or a narrowly scoped HID permission rule.")
            continue
        try:
            with HidrawTransport(path, pid, lock_dir / (device.id + ".lock"), expected_usb_path=device.usb_path or None) as transport:
                identity = query_identity(transport, usb_pid=pid)
                if identity.product_id == 0x6013 and identity.target_verified:
                    try:
                        paired = query_paired_identity(transport, receiver_pid=identity.product_id, timeout=0.5)
                        if 0 < paired.version_raw < 0xFFFF:
                            device.paired_firmware_version = paired.version
                    except (OSError, ValueError, TimeoutError, RuntimeError):
                        pass
            device.firmware_pid = identity.product_id if identity.target_verified else None
            device.hardware_revision = identity.hardware_revision
            device.firmware_version = identity.version
            model = model_for_identity(identity.product_id) if identity.target_verified else None
            device.model_id = model.id if model else None
            device.role = model.role if model else "unknown"
            if model:
                device.name = model.name
                device.status = "identified"
                device.reason = model.support
            elif identity.target_verified:
                device.status = "unsupported"
                device.reason = f"The device reports firmware target {identity.product_id:04x}, which has not been validated."
            else:
                device.status = "ambiguous"
                device.reason = "Firmware version read successfully; the exact controller/receiver variant is unresolved. Check the model label before browsing firmware."
            return device
        except (OSError, ValueError, TimeoutError, RuntimeError) as error:
            errors.append(clean_text(str(error)))
    device.status = "unavailable"
    device.reason = errors[-1] if errors else "Could not read firmware identity."
    return device
