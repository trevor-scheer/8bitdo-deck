import fcntl
import os
import select
import stat
import struct
import time
from pathlib import Path


class HidrawTransport:
    """Exclusive, bounded HID access with identity checked on the opened fd."""

    def __init__(self, path: Path, pid: int, lock_path: Path, *, expected_usb_path: str | None = None):
        self.path, self.pid, self.lock_path = path, pid, lock_path
        self.expected_usb_path = expected_usb_path
        self.fd: int | None = None
        self.lock_fd: int | None = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another update or query is using this device.") from None
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            opened = os.fstat(self.fd)
            if not stat.S_ISCHR(opened.st_mode):
                raise ValueError("The selected HID path is not a device.")
            if self.expected_usb_path is not None:
                physical = Path(f"/sys/dev/char/{os.major(opened.st_rdev)}:{os.minor(opened.st_rdev)}/device").resolve(strict=True)
                usb = next((p for p in physical.parents if (p / "idVendor").is_file()), None)
                if usb is None or usb != Path(self.expected_usb_path).resolve(strict=True):
                    raise ValueError("The HID node now belongs to a different USB port. Scan again.")
            info = bytearray(8)
            fcntl.ioctl(self.fd, 0x80084803, info, True)  # HIDIOCGRAWINFO, Linux UAPI
            bus, vendor, product = struct.unpack("IHH", info)
            if (bus, vendor, product) != (3, 0x2DC8, self.pid):
                raise ValueError("The USB device changed; scan again.")
            size_buffer = bytearray(4)
            fcntl.ioctl(self.fd, 0x80044801, size_buffer, True)  # HIDIOCGRDESCSIZE
            size = struct.unpack("I", size_buffer)[0]
            if not 0 < size <= 4096:
                raise ValueError("Invalid HID report descriptor size.")
            descriptor_buffer = bytearray(struct.pack("I", size) + bytes(4096))
            fcntl.ioctl(self.fd, 0x90044802, descriptor_buffer, True)  # HIDIOCGRDESC
            self._check_descriptor(bytes(descriptor_buffer[4:4 + size]))
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        for attribute in ("fd", "lock_fd"):
            fd = getattr(self, attribute)
            if fd is not None:
                os.close(fd)
                setattr(self, attribute, None)

    def send(self, report: bytes) -> None:
        if self.fd is None:
            raise RuntimeError("The HID connection is closed.")
        self._check_output(report)
        # The vendor discards queued input before a request; stale replies must
        # not authorize a later transaction. Bound draining on a noisy device.
        for _ in range(128):
            if not select.select([self.fd], [], [], 0)[0]:
                break
            try:
                if not os.read(self.fd, 65):
                    raise OSError("The device disconnected.")
            except BlockingIOError:
                break
        else:
            raise RuntimeError("The device input queue is busy. Close other controller software and retry.")
        if not select.select([], [self.fd], [], 2.0)[1]:
            raise TimeoutError("The device did not accept the request.")
        if os.write(self.fd, report) != len(report):
            raise OSError("Incomplete HID write; operation stopped.")

    def receive(self, timeout: float) -> bytes:
        if self.fd is None:
            raise RuntimeError("The HID connection is closed.")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise TimeoutError("The device did not reply. Check its USB connection.")
            try:
                report = os.read(self.fd, 65)
            except BlockingIOError:
                continue
            if not report:
                raise OSError("The device disconnected.")
            return report

    def _check_descriptor(self, descriptor: bytes) -> None:
        from .discovery import report_layout
        pages, inputs, outputs = report_layout(descriptor)
        if not (pages & {0xFFA0, 0xFF7A, 0x008C} and 0x02 in inputs and 0x81 in outputs):
            raise ValueError("The opened HID interface does not match the control protocol.")

    def _check_output(self, report: bytes) -> None:
        if len(report) != 64 or report[0] != 0x81:
            raise ValueError("Expected a 64-byte vendor output report.")


class BootHidrawTransport(HidrawTransport):
    """Unnumbered boot reports, bound to the original device's physical USB port.

    A shared boot USB PID is not a firmware identity. Callers must query and
    verify the bootloader's product ID before any update transaction.
    """

    def __init__(self, path: Path, pid: int, lock_path: Path, *, expected_usb_path: str):
        if pid not in (0x3208, 0x3002) or not expected_usb_path:
            raise ValueError("A known boot USB personality and original USB port are required.")
        super().__init__(path, pid, lock_path, expected_usb_path=expected_usb_path)

    def _check_descriptor(self, descriptor: bytes) -> None:
        from .discovery import report_layout
        pages, inputs, outputs = report_layout(descriptor, report_bits=512, unnumbered=True)
        if not (any(page >= 0xFF00 for page in pages) and inputs == {0} and outputs == {0}):
            raise ValueError("The boot interface must have unnumbered 64-byte vendor reports.")

    def _check_output(self, report: bytes) -> None:
        # hidraw requires the synthetic zero report selector on writes only.
        if len(report) != 65 or report[0] != 0:
            raise ValueError("Expected a zero report selector and 64 boot payload bytes.")
