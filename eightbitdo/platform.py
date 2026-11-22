"""Host integration boundary for the shared updater service.

Protocol transactions and write authorization remain in the service/backends.
This adapter owns device access, worker execution, and desktop integration.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from .access import RecoveryAccess
from .connection import require_boot_access, wait_for_application, wait_for_boot
from .discovery import discover, inspect_device
from .fwupd import Fwupd
from .runtime import external_environment, frozen_application
from .transport import HidrawTransport


@dataclass(frozen=True)
class WorkerLaunch:
    command: list[str]
    environment: dict[str, str]
    directory: Path


def process_start(pid: int) -> str:
    if sys.platform in {"darwin", "win32"}:
        import psutil
        try:
            return str(psutil.Process(pid).create_time())
        except psutil.Error as error:
            raise OSError("The worker process is unavailable.") from error
    # comm can contain spaces/parentheses; fields after its final ')' start at 3.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


class Platform:
    """Worker lifecycle shared by host adapters."""

    native_write = False

    def require_write_support(self):
        if not self.native_write:
            from .hidapi_backend import WRITE_UNAVAILABLE
            raise RuntimeError(WRITE_UNAVAILABLE)

    def worker_running(self, pid, started) -> bool:
        if type(pid) is not int or pid <= 0 or not isinstance(started, str):
            return False
        try:
            return process_start(pid) == started
        except (OSError, IndexError):
            return False

    def worker_launch(self, state_dir: Path, job_id: str, action: str, *, inhibit: bool) -> WorkerLaunch:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id) or action not in {"prepare", "install", "recover"}:
            raise ValueError("Invalid worker request.")
        state_dir = state_dir.resolve()
        environment = external_environment()
        if frozen_application():
            executable = str(Path(sys.executable).resolve())
            command = [executable, "--worker", str(state_dir), job_id, action]
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            environment.pop("PYTHONPATH", None)
            directory = state_dir
            service_environment = {"PYINSTALLER_RESET_ENVIRONMENT": "1", "PYTHONPATH": ""}
        else:
            source_root = Path(__file__).resolve().parent.parent
            executable = sys.executable
            if getattr(sys, "frozen", False):
                # Decky's frozen host is not a Python interpreter or our CLI.
                executable = shutil.which("python3")
                if not executable:
                    raise RuntimeError("The plugin host needs python3 to launch updater workers.")
            command = [executable, "-m", "eightbitdo.worker", str(state_dir), job_id, action]
            environment["PYTHONPATH"] = str(source_root)
            directory = source_root
            service_environment = {"PYTHONPATH": str(source_root)}
        if inhibit:
            self.require_write_support()
            inhibitor = shutil.which("systemd-inhibit")
            launcher = shutil.which("systemd-run")
            if not inhibitor or not launcher:
                raise RuntimeError("Cannot prevent suspend during the firmware update: user-session tools are unavailable.")
            unit = f"8bitdo-deck-{job_id}-{uuid.uuid4().hex[:12]}"
            # The service manager expands dollars in ExecStart arguments. Doubling
            # them preserves literal paths on systemd versions before 254 too.
            command = [argument.replace("$", "$$") for argument in command]
            # A user service avoids Decky's system-service authorization context.
            # Pass the frozen restart flag explicitly: the manager does not inherit it.
            command = [launcher, "--user", "--collect", "--wait", "--pipe", "--no-ask-password",
                       "--service-type=exec", "--unit=" + unit,
                       "--working-directory=" + str(directory),
                       *[f"--setenv={key}={value}" for key, value in service_environment.items()],
                       inhibitor, "--no-ask-password", "--what=sleep", "--mode=block", "--who=8BitDo Updater",
                       "--why=Updating controller firmware", *command]
        return WorkerLaunch(command, environment, directory)

    def start_worker(self, launch: WorkerLaunch, log_path: Path) -> None:
        options = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                   if sys.platform == "win32" else {"start_new_session": True})
        with log_path.open("ab") as log:
            subprocess.Popen(
                launch.command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                cwd=launch.directory, env=launch.environment, close_fds=True, **options,
            )


class LinuxPlatform(Platform):
    """Concrete host services; inject another adapter into Service when porting."""

    native_write = True

    def __init__(self):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("8BitDo Updater currently supports Linux hosts only.")

    def discover(self):
        return discover()

    def inspect_device(self, device, lock_dir):
        return inspect_device(device, lock_dir)

    def open_hid(self, *args, **kwargs):
        return HidrawTransport(*args, **kwargs)

    def require_boot_access(self, device):
        return require_boot_access(device)

    def wait_for_boot(self, *args, **kwargs):
        return wait_for_boot(*args, **kwargs)

    def wait_for_application(self, *args, **kwargs):
        return wait_for_application(*args, **kwargs)

    def recovery_access(self, state_dir):
        return RecoveryAccess(state_dir)

    def firmware_service(self):
        return Fwupd()

    def open_official_updater(self, url: str) -> dict:
        if os.geteuid() == 0:
            return {"opened": False, "error": "Run the updater as the desktop user to open a browser."}
        environment = external_environment()
        environment.setdefault("DISPLAY", ":0")
        environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        environment.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
        try:
            command = None
            flatpak = shutil.which("flatpak")
            if flatpak:
                installed = subprocess.run([flatpak, "info", "com.google.Chrome"], capture_output=True,
                                           timeout=10, env=environment)
                if installed.returncode == 0:
                    command = [flatpak, "run", "com.google.Chrome", "--new-window", url]
            if command is None:
                opener = shutil.which("xdg-open")
                if not opener:
                    return {"opened": False, "error": f"No desktop browser launcher is available. Open {url} in a WebHID-capable browser."}
                command = [opener, url]
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=environment, start_new_session=True)
            time.sleep(0.2)
            if process.poll() not in (None, 0):
                return {"opened": False, "error": f"The browser could not open. Open {url} in a WebHID-capable browser."}
            return {"opened": True}
        except (OSError, subprocess.TimeoutExpired):
            return {"opened": False, "error": f"Could not launch a browser. Open {url} in a WebHID-capable browser."}


class DesktopPlatform(Platform):
    """macOS/Windows discovery and read-only identity queries through HIDAPI."""

    def __init__(self):
        if sys.platform not in {"darwin", "win32"}:
            raise RuntimeError("The desktop HIDAPI adapter requires macOS or Windows.")

    def discover(self):
        from .hidapi_backend import discover_hidapi
        return discover_hidapi()

    def inspect_device(self, device, lock_dir):
        from .hidapi_backend import inspect_hidapi
        return inspect_hidapi(device, lock_dir)

    def open_hid(self, *args, **kwargs):
        from .hidapi_backend import HidapiTransport
        return HidapiTransport(*args, **kwargs)

    def recovery_access(self, state_dir):
        return UnavailableRecoveryAccess()

    def firmware_service(self):
        return UnavailableFwupd()

    def require_boot_access(self, *args, **kwargs):
        self.require_write_support()

    wait_for_boot = require_boot_access
    wait_for_application = require_boot_access

    def open_official_updater(self, url):
        import webbrowser
        try:
            opened = webbrowser.open(url, new=2)
        except (OSError, webbrowser.Error):
            opened = False
        return {"opened": True} if opened else {
            "opened": False, "error": f"Open {url} in a WebHID-capable browser."
        }


class UnavailableRecoveryAccess:
    def status(self):
        from .hidapi_backend import WRITE_UNAVAILABLE
        return {"configured": False, "pending": False, "can_setup": False, "message": WRITE_UNAVAILABLE}

    def setup(self):
        raise RuntimeError(self.status()["message"])


class UnavailableFwupd:
    def devices(self):
        return []

    def releases(self, *args, **kwargs):
        from .fwupd import FwupdError
        raise FwupdError("fwupd installation is supported only on Linux.")

    require_device = releases
    install = releases


def platform_capabilities() -> dict[str, bool]:
    supported = sys.platform.startswith("linux") or sys.platform in {"darwin", "win32"}
    linux = sys.platform.startswith("linux")
    return {
        "discovery": supported, "identity_query": supported, "firmware_download": supported,
        "native_write": linux, "recovery": linux, "fwupd": linux,
        "permission_setup": linux, "wireless_probe": linux,
    }


def current_platform():
    if sys.platform.startswith("linux"):
        return LinuxPlatform()
    if sys.platform in {"darwin", "win32"}:
        return DesktopPlatform()
    raise RuntimeError("8BitDo Updater supports Linux, macOS, and Windows.")
