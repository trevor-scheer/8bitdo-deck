import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .catalog import Catalog
from .discovery import Device, clean_text, discover, inspect_device
from .fwupd import Fwupd, FwupdError
from .jobs import ACTIVE_STATES, JobStore, default_state_dir, public_job
from .protocol import flash_application, parse_firmware, query_identity
from .qualifications import qualification_for
from .registry import MODELS, get_model
from .transport import HidrawTransport


OFFICIAL_UPDATER = "https://web.8bitdo.com/"


class Service:
    def __init__(self, state_dir: Path | None = None, *, catalog=None, fwupd=None):
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.store = JobStore(self.state_dir)
        self.catalog = catalog if catalog is not None else Catalog()
        self.fwupd = fwupd if fwupd is not None else Fwupd()

    def _devices(self, probe: bool = True) -> tuple[list[Device], list[str]]:
        devices = discover()
        if probe:
            devices = [inspect_device(d, self.state_dir / "locks") for d in devices]
            paired_devices = [Device(
                id="paired-" + d.id.removeprefix("usb-"),
                name="Ultimate 2 Wireless controller (via receiver)", vid="2dc8", pid="wireless",
                role="controller", model_id="ultimate2-controller", firmware_version=d.paired_firmware_version,
                status="wireless", can_update=False,
                reason="Firmware version read wirelessly through the receiver. Native firmware installation is not yet validated.",
            ) for d in devices if d.paired_firmware_version]
            devices.extend(paired_devices)
        warnings = []
        try:
            devices.extend(self.fwupd.devices())
        except FwupdError as error:
            warnings.append(str(error))
        return devices, warnings

    def scan_devices(self) -> dict:
        devices, warnings = self._devices()
        return {"devices": [d.public() for d in devices], "warnings": warnings}

    def probe_wireless(self) -> dict:
        from .wireless import query_wireless_target

        results = []
        for device in discover():
            if int(device.pid, 16) not in (0x310B, 0x6013):
                continue
            result = {"id": device.id, "can_update": False}
            try:
                if not device.hid_paths:
                    raise ValueError("No compatible USB control interface")
                with HidrawTransport(device.hid_paths[0], int(device.pid, 16), self.state_dir / "locks" / (device.id + ".lock"), expected_usb_path=device.usb_path) as transport:
                    target = query_wireless_target(transport, usb_pid=int(device.pid, 16))
                result.update(status="radio_identity_verified", receiver_version=target.receiver.version,
                              controller_version=target.controller.version, controller_pid=f"{target.controller.pid:04x}",
                              battery_percent=target.status.battery_percent,
                              message="Radio firmware identity verified. No firmware was written; installation is not qualified.")
            except (OSError, ValueError, TimeoutError, RuntimeError) as error:
                result.update(status="not_verified", reason=clean_text(str(error)))
            results.append(result)
        return {"devices": results}

    def get_models(self) -> list[dict]:
        models = [model.public() for model in MODELS]
        try:
            for device in self.fwupd.devices():
                models.append({
                    "id": device.model_id, "name": device.name, "role": device.role,
                    "instructions": "Keep this USB device connected. Follow any bootloader instructions supplied by fwupd.",
                    "support": device.reason,
                })
        except FwupdError:
            pass
        return models

    def get_releases(self, model_id: str) -> dict:
        try:
            if model_id.startswith("fwupd-"):
                return {"releases": self.fwupd.releases(model_id)}
            model = get_model(model_id)
            releases = self.catalog.list_releases(model.catalog_type)
            return {"releases": [{
                "id": r.key, "version": r.version, "date": r.released_at,
                "notes": r.notes, "downloadable": True,
            } for r in releases]}
        except (OSError, ValueError, RuntimeError) as error:
            return {"releases": [], "error": clean_text(str(error), 600)}

    def _spawn(self, job: dict, action: str) -> None:
        executable = shutil.which("python3") or sys.executable
        source_root = Path(__file__).resolve().parent.parent
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(source_root)
        command = [executable, "-m", "eightbitdo.worker", str(self.state_dir), job["id"], action]
        if action == "install" and job.get("backend") == "hid":
            inhibitor = shutil.which("systemd-inhibit")
            if not inhibitor:
                raise RuntimeError("Cannot prevent suspend during the native update.")
            command = [inhibitor, "--what=sleep", "--mode=block", "--who=8BitDo Deck",
                       "--why=Updating controller firmware", *command]
        job["worker_pid"] = None
        job["started_at"] = time.time()
        self.store.save(job)
        try:
            with (self.store.directory / (job["id"] + ".log")).open("ab") as log:
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    cwd=source_root, env=environment, start_new_session=True, close_fds=True,
                )
        except OSError as error:
            job.update(state="failed", message="Could not start the background worker.", can_install=False)
            self.store.save(job)
            raise RuntimeError(job["message"]) from error

    def prepare_update(self, device_id: str, model_id: str, release_id: str) -> dict:
        try:
            if not all(isinstance(value, str) for value in (device_id, model_id, release_id)):
                raise ValueError("Invalid update selection.")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", release_id):
                raise ValueError("Invalid firmware release.")
            if model_id.startswith("fwupd-"):
                if device_id != model_id:
                    raise ValueError("Select the connected fwupd device before preparing its update.")
                self.fwupd.require_device(device_id)
            else:
                get_model(model_id)
                if device_id:
                    if not re.fullmatch(r"usb-[0-9a-f]{16}", device_id):
                        raise ValueError("Invalid USB device selection.")
                    devices, _ = self._devices()
                    device = next((d for d in devices if d.id == device_id), None)
                    if not device or device.model_id != model_id or device.status != "identified":
                        raise ValueError("Device identity does not match this firmware. Scan again, or browse the model without selecting a device.")
            job = self.store.create(device_id, model_id, release_id)
            self._spawn(job, "prepare")
            return {"job_id": job["id"]}
        except (OSError, ValueError, RuntimeError) as error:
            return {"error": clean_text(str(error), 600)}

    def get_job(self, job_id: str) -> dict:
        job = self.store.read(job_id)
        if job["state"] in ACTIVE_STATES and time.time() - job.get("started_at", job["created_at"]) > 10:
            pid = job.get("worker_pid")
            try:
                running = bool(pid) and Path(f"/proc/{pid}/stat").read_text().split()[21] == job.get("worker_start")
            except (OSError, IndexError):
                running = False
            if not running:
                # Preserve a concurrently finishing worker's result.
                try:
                    with self.store.lock(job_id):
                        job = self.store.read(job_id)
                        if job["state"] in ACTIVE_STATES:
                            job.update(state="failed", can_install=False, message="The worker stopped. Scan and verify the device before attempting another update.")
                            self.store.save(job)
                except RuntimeError:
                    pass
        return public_job(job)

    def install_update(self, job_id: str) -> dict:
        with self.store.lock(job_id):
            job = self.store.read(job_id)
            if job["state"] != "ready" or not job["can_install"] or job.get("backend") not in {"fwupd", "hid"}:
                raise ValueError("This firmware is not qualified for installation. Use the official updater for this model.")
            job.update(state="queued", can_install=False, progress=0, message="Starting the confirmed update. Keep the device connected.")
            self._spawn(job, "install")
            return public_job(job)

    def run_prepare(self, job: dict) -> None:
        if job["model_id"].startswith("fwupd-"):
            device = self.fwupd.require_device(job["device_id"])
            release = next((r for r in self.fwupd.releases(device.id) if r["id"] == job["release_id"]), None)
            if not release or not release["downloadable"]:
                raise ValueError("This release is no longer available with trusted fwupd metadata.")
            job.update(
                state="ready", progress=100, can_install=True, backend="fwupd", version=release["version"],
                target_label=f"{device.name} → {release['version']}",
                message="Trusted fwupd release selected. On confirmation, fwupd will download, verify, and install it for this device.",
            )
            self.store.save(job)
            return
        model = get_model(job["model_id"])
        release = next((r for r in self.catalog.list_releases(model.catalog_type) if r.key == job["release_id"]), None)
        if release is None:
            raise ValueError("The selected vendor release is no longer available. Refresh the release list.")
        job.update(state="downloading", progress=10, target_label=f"{model.name} → {release.version}", message="Downloading firmware from 8BitDo over HTTPS.")
        self.store.save(job)
        artifact = self.catalog.download(release, self.state_dir / "firmware")
        job.update(state="validating", progress=80, message="Checking firmware size, target header, and version.")
        self.store.save(job)
        # The catalog version uses decimal hundredths for the registered models.
        match = re.fullmatch(r"(\d+)\.(\d{1,2})", release.version)
        if not match:
            raise ValueError("The firmware version format is not supported for this model.")
        expected_version = int(match[1]) * 100 + int(match[2].ljust(2, "0"))
        data = Path(artifact.path).read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError("Cached firmware changed during validation.")
        image = parse_firmware(data, expected_pid=model.firmware_pid, expected_version=expected_version)
        job.update(
            state="blocked", progress=100, can_install=False, backend="hid",
            version=image.version, sha256=artifact.sha256, artifact_path=artifact.path,
            message="Firmware downloaded; model and version headers match. Native installation is awaiting hardware qualification. " + artifact.verification_note + " Use Open official updater to update this model.",
        )
        if job["device_id"]:
            device = self._native_device(job["device_id"], model.id)
            with HidrawTransport(device.hid_paths[0], int(device.pid, 16), self.state_dir / "locks" / (device.id + ".lock"), expected_usb_path=device.usb_path) as transport:
                identity = query_identity(transport, usb_pid=int(device.pid, 16))
            if qualification_for(image, identity) is not None:
                job.update(state="ready", can_install=True, original_version=identity.version,
                           message="The image matches a hardware qualification record. Review the target and version before installing.")
        self.store.save(job)

    def run_install(self, job: dict) -> None:
        if job.get("backend") == "hid":
            with self.store.lock("update-" + job["device_id"]):
                self._install_native(job)
            return
        if job.get("backend") != "fwupd" or not job["device_id"].startswith("fwupd-"):
            raise ValueError("This device has no qualified native installation path.")
        with self.store.lock(job["device_id"]):
            job.update(state="installing", progress=10, message="fwupd is downloading, verifying, and installing firmware. Keep the device connected.")
            self.store.save(job)
            self.fwupd.install(job["device_id"], job["release_id"], job["version"])
            job.update(state="verifying", progress=90, message="Checking the device's reported firmware version.")
            self.store.save(job)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                device = next((d for d in self.fwupd.devices() if d.id == job["device_id"]), None)
                if device and device.firmware_version == job["version"]:
                    job.update(state="complete", progress=100, message=f"Update complete. The device reports firmware {job['version']}.")
                    self.store.save(job)
                    return
                time.sleep(1)
            job.update(state="blocked", can_install=False, message="fwupd finished, but the new version could not be confirmed. Reconnect the device and scan; do not repeat the update yet.")
            self.store.save(job)

    def _native_device(self, device_id: str, model_id: str) -> Device:
        device = next((d for d in discover() if d.id == device_id), None)
        if not device:
            raise ValueError("The selected USB device disconnected. Scan again.")
        device = inspect_device(device, self.state_dir / "locks")
        if device.status != "identified" or device.model_id != model_id or not device.hid_paths:
            raise ValueError("The USB target no longer matches the selected firmware.")
        return device

    def _install_native(self, job: dict) -> None:
        model = get_model(job["model_id"])
        cache = (self.state_dir / "firmware").resolve()
        path = Path(job["artifact_path"])
        if path.is_symlink() or not path.resolve().is_relative_to(cache):
            raise ValueError("The firmware image is outside the validated cache.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != job["sha256"]:
            raise ValueError("The firmware image changed after preparation.")
        image = parse_firmware(data, expected_pid=model.firmware_pid)
        if image.version != job["version"]:
            raise ValueError("The firmware version changed after preparation.")
        device = self._native_device(job["device_id"], model.id)
        with HidrawTransport(device.hid_paths[0], int(device.pid, 16), self.state_dir / "locks" / (device.id + ".lock"), expected_usb_path=device.usb_path) as transport:
            identity = query_identity(transport, usb_pid=int(device.pid, 16))
            qualification = qualification_for(image, identity)
            if qualification is None or identity.version != job.get("original_version"):
                raise ValueError("The target or its hardware qualification changed. Prepare the update again.")
            job.update(state="installing", progress=0, can_install=False,
                       message="Installing the qualified image. Keep the device connected.")
            self.store.save(job)

            def progress(stage: str, done: int, total: int) -> None:
                percent = min(90, int(done * 90 / max(1, total)))
                if percent > job["progress"] or job.get("stage") != stage:
                    job.update(progress=max(job["progress"], percent), stage=stage,
                               message=f"Native update: {stage}. Keep USB connected.")
                    # Reporting failure must not interrupt a partially erased
                    # device. The initial durable job record precedes all writes.
                    self._save_progress(job)

            flash_application(transport, image, identity=identity, qualification=qualification, progress=progress)
        job.update(state="verifying", progress=95, message="Waiting for the same USB target and checking its firmware version.")
        self._save_progress(job)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                device = self._native_device(job["device_id"], model.id)
                if device.firmware_version == job["version"]:
                    job.update(state="complete", progress=100, message=f"Update complete. The device reports firmware {job['version']}.")
                    self._save_progress(job)
                    return
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(0.5)
        job.update(state="blocked", can_install=False, message="The image verified, but reconnection/version verification did not finish. Check the device before retrying.")
        self._save_progress(job)

    def _save_progress(self, job: dict) -> None:
        try:
            self.store.save(job)
        except (OSError, ValueError):
            job["reporting_error"] = "Progress storage failed; verify the device before retrying."

    def export_diagnostics(self) -> dict:
        devices, warnings = self._devices(probe=False)
        payload = {
            "schema_version": 1, "app_version": "0.1.3",
            "devices": [{k: v for k, v in d.public().items() if k in {
                "vid", "pid", "role", "firmware_version", "status", "can_update", "backend",
            }} for d in devices],
            "warnings": ["fwupd discovery was unavailable."] if warnings else [],
            "note": "No serial numbers, USB topology, firmware images, or personal paths are included.",
        }
        directory = self.state_dir / "diagnostics"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / ("diagnostics-" + str(time.time_ns()) + ".json")
        with path.open("x") as stream:
            json.dump(payload, stream, indent=2)
        return {"path": str(path)}

    def open_official_updater(self) -> dict:
        if os.geteuid() == 0:
            return {"opened": False, "error": "Run the plugin as the desktop user to open Chrome."}
        executable = shutil.which("flatpak")
        if not executable:
            return {"opened": False, "error": "Install Google Chrome from Discover, then open https://web.8bitdo.com/ in Desktop Mode."}
        try:
            installed = subprocess.run([executable, "info", "com.google.Chrome"], capture_output=True, timeout=10)
            if installed.returncode:
                return {"opened": False, "error": "Install Google Chrome from Discover to use the WebHID updater."}
            environment = dict(os.environ)
            environment.setdefault("DISPLAY", ":0")
            environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            environment.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
            process = subprocess.Popen(
                [executable, "run", "com.google.Chrome", "--new-window", OFFICIAL_UPDATER],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=environment, start_new_session=True,
            )
            time.sleep(0.2)
            if process.poll() not in (None, 0):
                return {"opened": False, "error": "Chrome could not open. Open https://web.8bitdo.com/ in Desktop Mode."}
            return {"opened": True}
        except (OSError, subprocess.TimeoutExpired):
            return {"opened": False, "error": "Could not launch Chrome. Open https://web.8bitdo.com/ in Desktop Mode."}
