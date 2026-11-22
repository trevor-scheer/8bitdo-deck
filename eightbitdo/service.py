import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from .catalog import Catalog
from .discovery import Device, clean_text
from .fwupd import FwupdError
from .platform import current_platform
from .jobs import ACTIVE_STATES, JobStore, default_state_dir, public_job
from .locking import shared_lock
from .legacy_protocol import (
    LegacySourceReviewedAuthorization, flash_boot_receiver, inspect_receiver_boot, request_receiver_boot,
    validate_receiver_authorization,
)
from .protocol import (
    DeviceIdentity, SourceReviewedAuthorization, flash_application,
    parse_firmware, query_identity, verify_authorization_sources,
)
from .recovery import finish_recovery, inspect_recovery
from .qualifications import qualification_for
from .registry import MODELS, get_model


OFFICIAL_UPDATER = "https://web.8bitdo.com/"


class Service:
    def __init__(self, state_dir: Path | None = None, *, catalog=None, fwupd=None, platform=None, coordination_dir=None):
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.store = JobStore(self.state_dir)
        self.coordination_dir = Path(coordination_dir) if coordination_dir is not None else default_state_dir()
        self.platform = platform if platform is not None else current_platform()
        self.catalog = catalog if catalog is not None else Catalog()
        self.fwupd = fwupd if fwupd is not None else self.platform.firmware_service()
        self.recovery_access = self.platform.recovery_access(self.state_dir)

    @contextmanager
    def _queue_guard(self):
        coordinator = JobStore(self.coordination_dir)
        with coordinator.lock("application-mutation"):
            if (self.coordination_dir / "app-update-pending.json").exists():
                raise RuntimeError("An application update is pending. Finish or resolve it before starting firmware jobs.")
            coordinator.register_state(self.state_dir)
            yield

    @contextmanager
    def worker_guard(self):
        # Wait for the queuing parent to release its exclusive launch lock.
        # Keep a shared lock until the worker has finished every device action.
        with shared_lock(self.coordination_dir / 'locks/application-mutation.lock'):
            if (self.coordination_dir / 'app-update-pending.json').exists():
                raise RuntimeError('An application update is pending. The firmware worker cannot start.')
            yield

    @contextmanager
    def app_update_guard(self):
        """Exclude job launch/replacement races and refuse running workers."""
        coordinator = JobStore(self.coordination_dir)
        with coordinator.lock("application-mutation"):
            states = coordinator.registered_states() | {self.state_dir.resolve(), self.coordination_dir.resolve()}
            for state in states:
                if not (state / 'jobs').is_dir():
                    # The exclusive canonical lock proves no worker is using
                    # a deleted temporary state directory. Late workers must
                    # acquire its shared counterpart and re-read their job.
                    continue
                store = JobStore(state)
                for path in store.directory.glob("*.json"):
                    # The worker holds this lock through its final disk write.
                    # Acquiring it also closes the startup/reconciliation race.
                    with store.lock(path.stem):
                        job = store.read(path.stem)
                        if self.platform.worker_running(job.get("worker_pid"), job.get("worker_start")):
                            raise RuntimeError("Wait for firmware jobs to finish before updating the application.")
                        if job.get("state") in ACTIVE_STATES:
                            if time.time() - job.get('started_at', job['created_at']) <= 10:
                                raise RuntimeError("Wait for firmware jobs to finish before updating the application.")
                            job.update(state='failed', can_install=False, recovery_ready=False, recovery_confirmed=False,
                                       message='The worker stopped. Scan and verify the device before attempting another update.')
                            store.save(job)
            # All registered jobs are now terminal and no worker owns a lock.
            # Future queues register again under this same canonical lock.
            coordinator.write_registered_states(set())
            yield

    def check_app_update(self, channel: str):
        from .selfupdate import AppUpdater
        return AppUpdater(self.state_dir, guard=self.app_update_guard).check(channel)

    def apply_app_update(self, channel: str, version: str, confirmed: bool):
        from .selfupdate import AppUpdater
        return AppUpdater(self.state_dir, guard=self.app_update_guard).apply(channel, version, confirmed)

    def recover_app_update(self, confirmed: bool):
        from .selfupdate import AppUpdater
        return AppUpdater(self.state_dir, guard=self.app_update_guard).recover(confirmed)

    def get_recovery_access(self) -> dict:
        return self.recovery_access.status()

    def setup_recovery_access(self) -> dict:
        return self.recovery_access.setup()

    def _devices(self, probe: bool = True) -> tuple[list[Device], list[str]]:
        devices = self.platform.discover()
        if probe:
            devices = [self.platform.inspect_device(d, self.state_dir / "locks") for d in devices]
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
        if self.platform.native_write is False:
            raise RuntimeError("Extended wireless probing is not validated on this operating system. Receiver status is included in device scans.")
        from .wireless import query_wireless_target

        results = []
        for device in self.platform.discover():
            if int(device.pid, 16) not in (0x310B, 0x6013):
                continue
            result = {"id": device.id, "can_update": False}
            try:
                if not device.hid_paths:
                    raise ValueError("No compatible USB control interface")
                with self.platform.open_hid(device.hid_paths[0], int(device.pid, 16), self.state_dir / "locks" / (device.id + ".lock"), expected_usb_path=device.usb_path) as transport:
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
        self.store.path(job["id"])
        launch = self.platform.worker_launch(
            self.state_dir, job["id"], action,
            inhibit=action == "install" or (action == "recover" and job.get("backend") == "hid"),
        )
        job["worker_action"] = action
        job["worker_pid"] = None
        job["started_at"] = time.time()
        self.store.save(job)
        try:
            self.platform.start_worker(launch, self.store.directory / (job["id"] + ".log"))
        except OSError as error:
            job.update(state="failed", message="Could not start the background worker.", can_install=False)
            self.store.save(job)
            raise RuntimeError(job["message"]) from error

    def prepare_update(self, device_id: str, model_id: str, release_id: str) -> dict:
        with self._queue_guard():
            return self._prepare_update(device_id, model_id, release_id)

    def _prepare_update(self, device_id: str, model_id: str, release_id: str) -> dict:
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
            running = self.platform.worker_running(pid, job.get("worker_start"))
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
        return self._public_recovery_job(job)

    def install_update(self, job_id: str) -> dict:
        self.platform.require_write_support()
        with self._queue_guard():
            return self._install_update(job_id)

    def _install_update(self, job_id: str) -> dict:
        with self.store.lock(job_id):
            job = self.store.read(job_id)
            if job["state"] != "ready" or not job["can_install"] or job.get("backend") not in {"fwupd", "hid"}:
                raise ValueError("This firmware is not qualified for installation. No firmware was written.")
            if job.get("backend") == "hid" and not self.recovery_access.status()['configured']:
                raise ValueError("Configure receiver recovery access before installing firmware.")
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
            message="Firmware downloaded; model and version headers match. Native installation is awaiting a reviewed update authorization. " + artifact.verification_note + " Firmware on the device is unchanged.",
        )
        if self.platform.native_write is False:
            from .hidapi_backend import WRITE_UNAVAILABLE
            job["message"] = "Firmware downloaded and header validated. " + WRITE_UNAVAILABLE + " " + artifact.verification_note
        elif job["device_id"]:
            device = self._native_device(job["device_id"], model.id)
            with self.platform.open_hid(device.hid_paths[0], int(device.pid, 16), self.state_dir / "locks" / (device.id + ".lock"), expected_usb_path=device.usb_path) as transport:
                identity = query_identity(transport, usb_pid=int(device.pid, 16))
                authorization = qualification_for(image, identity)
                if authorization is not None:
                    job.update(evidence_level=authorization.evidence_level, evidence=authorization.evidence)
                    if self.recovery_access.status()['configured']:
                        if isinstance(authorization, LegacySourceReviewedAuthorization):
                            validate_receiver_authorization(image, identity, authorization)
                        else:
                            verify_authorization_sources(transport, image, identity=identity, qualification=authorization)
                        job.update(state="ready", can_install=True, original_version=identity.version,
                                   message="Firmware and device checks passed. Review the target, evidence level, and version before installing.")
                    else:
                        job['message'] = 'Firmware verified. Set up receiver recovery access, then prepare this update again.'
        self.store.save(job)

    def run_install(self, job: dict) -> None:
        self.platform.require_write_support()
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
        device = next((d for d in self.platform.discover() if d.id == device_id), None)
        if not device:
            raise ValueError("The selected USB device disconnected. Scan again.")
        device = self.platform.inspect_device(device, self.state_dir / "locks")
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
        if not self.recovery_access.status()['configured']:
            raise ValueError("Configure receiver recovery access before installing firmware.")
        device = self._native_device(job["device_id"], model.id)
        with self.platform.open_hid(device.hid_paths[0], int(device.pid, 16), self.state_dir / "locks" / (device.id + ".lock"), expected_usb_path=device.usb_path) as transport:
            identity = query_identity(transport, usb_pid=int(device.pid, 16))
            qualification = qualification_for(image, identity)
            if qualification is None or identity.version != job.get("original_version"):
                raise ValueError("The target or its update authorization changed. Prepare the update again.")
            if job.get('evidence_level') != qualification.evidence_level:
                raise ValueError("The update evidence changed. Prepare and review the update again.")
            legacy_receiver = isinstance(qualification, LegacySourceReviewedAuthorization)
            if legacy_receiver:
                validate_receiver_authorization(image, identity, qualification)
                self.platform.require_boot_access(device)
            job.update(state="installing", progress=0, can_install=False,
                       original_version_raw=identity.version_raw, original_usb_path=device.usb_path,
                       original_hardware_revision=identity.hardware_revision, recovery_ready=False,
                       message="Installing the reviewed image. Keep the device connected.")
            self.store.save(job)

            def progress(stage: str, done: int, total: int) -> None:
                percent = min(90, int(done * 90 / max(1, total)))
                if percent > job["progress"] or job.get("stage") != stage:
                    job.update(progress=max(job["progress"], percent), stage=stage,
                               message=f"Native update: {stage}. Keep USB connected.")
                    # Reporting failure must not interrupt a partially erased
                    # device. The initial durable job record precedes all writes.
                    self._save_progress(job)

            if legacy_receiver:
                progress('entering update mode', 0, len(image.payload))
                request_receiver_boot(transport, identity=identity)
            else:
                flash_application(transport, image, identity=identity, qualification=qualification, progress=progress)
        if legacy_receiver:
            # The runtime fd and its lock must close before opening the new HID node.
            with self.platform.wait_for_boot(device, self.state_dir / 'locks') as boot:
                flash_boot_receiver(boot.transport, image, identity=identity, boot_usb_pid=boot.usb_pid,
                                    qualification=qualification, progress=progress)
        job.update(state="verifying", progress=95, message="Waiting for the same USB target and checking its firmware version.")
        self._save_progress(job)
        try:
            self.platform.wait_for_application(
                device, self.state_dir / "locks", expected_product_id=image.product_id,
                expected_version_raw=image.version_raw & 0xFFFF,
            )
        except (OSError, ValueError, RuntimeError):
            job.update(state="blocked", can_install=False, message="The image verified, but reconnection/version verification did not finish. Check the device before retrying.")
        else:
            job.update(state="complete", progress=100, message=f"Update complete. The device reports firmware {job['version']}.")
        self._save_progress(job)

    def _recovery_context(self, job: dict, *, queued: bool = False):
        states = {"queued"} if queued else {"failed", "blocked"}
        if (
            job.get("state") not in states or job.get("backend") != "hid"
            or job.get("model_id") not in {"ultimate2-receiver", "ultimate-bluetooth-receiver"}
            or job.get("worker_action") not in {"install", "recover"}
            or job.get("evidence_level") != "source-reviewed-experimental"
            or type(job.get("original_version_raw")) is not int
            or not 0 < job["original_version_raw"] <= 0xFFFF
        ):
            raise ValueError("This job has no reviewed receiver recovery path.")
        port = job.get("original_usb_path")
        if not isinstance(port, str) or not Path(port).is_absolute() or os.path.normpath(port) != port:
            raise ValueError("The original physical USB port was not recorded.")
        expected_id = "usb-" + hashlib.sha256(port.encode()).hexdigest()[:16]
        if job.get("device_id") != expected_id:
            raise ValueError("The recovery job does not match its original USB port.")
        product_id = get_model(job["model_id"]).firmware_pid
        original = DeviceIdentity(product_id, job["original_version_raw"],
                                  hardware_revision=job.get("original_hardware_revision"))
        if job.get("original_version") != original.version:
            raise ValueError("The original firmware version record is inconsistent.")
        cache = (self.state_dir / "firmware").resolve()
        path = Path(job.get("artifact_path", ""))
        if path.is_symlink() or not path.resolve().is_relative_to(cache):
            raise ValueError("The firmware image is outside the validated cache.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != job.get("sha256"):
            raise ValueError("The firmware image changed after preparation.")
        image = parse_firmware(data, expected_pid=product_id)
        if image.version != job.get("version"):
            raise ValueError("The firmware version changed after preparation.")
        authorization = qualification_for(image, original)
        expected_type = LegacySourceReviewedAuthorization if product_id == 0x6008 else SourceReviewedAuthorization
        if (
            type(authorization) is not expected_type
            or authorization.evidence_level != job.get("evidence_level")
            or authorization.evidence != job.get("evidence")
        ):
            raise ValueError("The compiled source-reviewed recovery authorization changed or is unavailable.")
        if expected_type is LegacySourceReviewedAuthorization:
            validate_receiver_authorization(image, original, authorization)
        return image, authorization

    def _public_recovery_job(self, job: dict) -> dict:
        result = public_job(job)
        try:
            self._recovery_context(job)
        except (OSError, ValueError, TypeError, RuntimeError):
            result.update(can_check_recovery=False, recovery_ready=False)
        else:
            result["can_check_recovery"] = True
        return result

    def _recovery_device(self, job: dict) -> Device:
        device = next((d for d in self.platform.discover() if d.id == job["device_id"]), None)
        runtime_pids = {"3109"} if job["model_id"] == "ultimate-bluetooth-receiver" else {"6013", "310b"}
        if (
            device is None or device.usb_path != job["original_usb_path"]
            or device.vid != "2dc8" or device.backend != "hid"
            or device.pid not in runtime_pids | {"3208"}
        ):
            raise ValueError("The original receiver is not connected on its recorded USB port.")
        return device

    def _read_recovery_identity(self, device: Device, job: dict) -> DeviceIdentity:
        runtime_pids = {"3109"} if job["model_id"] == "ultimate-bluetooth-receiver" else {"6013", "310b"}
        if device.pid not in runtime_pids or not device.hid_paths:
            raise ValueError("The receiver has not returned to application mode.")
        with self.platform.open_hid(
            device.hid_paths[0], int(device.pid, 16), self.state_dir / "locks" / (device.id + ".lock"),
            expected_usb_path=job["original_usb_path"],
        ) as transport:
            identity = query_identity(transport, usb_pid=int(device.pid, 16))
        if (
            identity.product_id != get_model(job["model_id"]).firmware_pid
            or not identity.target_verified or identity.beta
            or identity.hardware_revision != job.get("original_hardware_revision")
        ):
            raise ValueError("The returning device is not the expected stable receiver.")
        return identity

    def _record_recovery_version(self, job: dict, identity: DeviceIdentity) -> None:
        job.update(can_install=False, recovery_ready=False, recovery_confirmed=False, progress=100)
        if identity.version == job["version"]:
            job.update(state="complete", recovery_action="target-confirmed",
                       message=f"The same receiver reports firmware {identity.version}. The target version is verified.")
        elif identity.version_raw == job["original_version_raw"]:
            job.update(state="blocked", recovery_action="source-unchanged",
                       message=f"The receiver is available with its original firmware {identity.version}; the update was not completed.")
        else:
            raise ValueError("The receiver reports an unexpected firmware version. Recovery was not confirmed.")

    def check_recovery(self, job_id: str) -> dict:
        """Inspect an eligible failed install without changing device mode or resetting."""
        self.platform.require_write_support()
        with self.store.lock(job_id):
            job = self.store.read(job_id)
            image, authorization = self._recovery_context(job)
            job.update(recovery_ready=False, recovery_action=None, can_install=False)
            self.store.save(job)
            if not self.recovery_access.status()["configured"]:
                raise ValueError("Configure receiver recovery access before checking recovery.")
            with self.store.lock("update-" + job["device_id"]):
                device = self._recovery_device(job)
                if device.pid != "3208":
                    self._record_recovery_version(job, self._read_recovery_identity(device, job))
                else:
                    with self.platform.wait_for_boot(device, self.state_dir / "locks") as boot:
                        if boot.usb_pid != 0x3208:
                            raise ValueError("Unexpected receiver recovery USB personality.")
                        if isinstance(authorization, LegacySourceReviewedAuthorization):
                            inspect_receiver_boot(boot.transport, usb_pid=boot.usb_pid,
                                                  expected_initialization=authorization.initialization)
                            job.update(state="blocked", recovery_ready=True, recovery_action="reflash",
                                       message="Receiver boot identity and initialization match this attempted update. Recovery requires erasing and reinstalling this job's exact cached firmware image. No firmware was written or reset sent.")
                        else:
                            status = inspect_recovery(boot.transport, image, authorization=authorization)
                            job.update(state="blocked", recovery_ready=status.can_reset, recovery_action=status.action,
                                       message=status.reason + ". No reset was sent.")
            self.store.save(job)
            return self._public_recovery_job(job)

    def recover_update(self, job_id: str, confirmed: bool = False) -> dict:
        self.platform.require_write_support()
        with self._queue_guard():
            return self._recover_update(job_id, confirmed)

    def _recover_update(self, job_id: str, confirmed: bool = False) -> dict:
        if confirmed is not True:
            raise ValueError("Review the recovery check and explicitly confirm recovery first.")
        with self.store.lock(job_id):
            job = self.store.read(job_id)
            _, authorization = self._recovery_context(job)
            if not self.recovery_access.status()["configured"]:
                raise ValueError("Configure receiver recovery access before recovering firmware.")
            actions = {"reflash"} if isinstance(authorization, LegacySourceReviewedAuthorization) else {"finish-pending", "return-installed"}
            if job.get("recovery_ready") is not True or job.get("recovery_action") not in actions:
                raise ValueError("Run a successful recovery check before confirming recovery.")
            job.update(state="queued", recovery_ready=False, recovery_confirmed=True, can_install=False,
                       progress=0, message="Starting confirmed recovery. Keep the receiver connected.")
            self._spawn(job, "recover")
            return public_job(job)

    def run_recover(self, job: dict) -> None:
        self.platform.require_write_support()
        if job.get("worker_action") != "recover" or job.get("recovery_confirmed") is not True:
            raise ValueError("This job has no confirmed recovery request.")
        image, authorization = self._recovery_context(job, queued=True)
        if not self.recovery_access.status()["configured"]:
            raise ValueError("Configure receiver recovery access before recovering firmware.")
        with self.store.lock("update-" + job["device_id"]):
            device = self._recovery_device(job)
            if device.pid != "3208":
                self._record_recovery_version(job, self._read_recovery_identity(device, job))
                self.store.save(job)
                return
            if isinstance(authorization, LegacySourceReviewedAuthorization):
                self._run_legacy_recovery(job, device, image, authorization)
                return
            job.update(state="verifying", recovery_ready=False, can_install=False,
                       message="Rechecking receiver identity, bootloader, metadata, and firmware before recovery.")
            self.store.save(job)
            with self.platform.wait_for_boot(device, self.state_dir / "locks") as boot:
                if boot.usb_pid != 0x3208:
                    raise ValueError("Unexpected receiver recovery USB personality.")
                result = finish_recovery(boot.transport, image, authorization=authorization)
            job.update(recovery_action=result.action, recovery_confirmed=False,
                       message="Recovery reset sent. Waiting for the same receiver and verifying its firmware version.")
            self._save_progress(job)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    current = self._recovery_device(job)
                    identity = self._read_recovery_identity(current, job)
                    if result.vendor_copy_expected and identity.version != job["version"]:
                        raise ValueError("The committed target version has not been verified.")
                    self._record_recovery_version(job, identity)
                    self._save_progress(job)
                    return
                except (OSError, ValueError, RuntimeError):
                    pass
                time.sleep(0.5)
            job.update(state="blocked", recovery_ready=False, can_install=False,
                       message="Recovery reset was sent, but the expected receiver version was not confirmed. Check recovery again; no reset was retried.")
            self._save_progress(job)

    def _run_legacy_recovery(self, job, device, image, authorization) -> None:
        if job.get("recovery_action") != "reflash":
            raise ValueError("This job has no confirmed receiver reflash request.")
        original = DeviceIdentity(0x6008, job["original_version_raw"],
                                  hardware_revision=job.get("original_hardware_revision"))
        job.update(state="verifying", recovery_ready=False, can_install=False,
                   message="Rechecking the original receiver boot interface before reinstalling the cached firmware.")
        self.store.save(job)

        def progress(stage: str, done: int, total: int) -> None:
            percent = min(90, int(done * 90 / max(1, total)))
            if percent > job["progress"] or job.get("stage") != stage:
                job.update(state="installing", progress=max(job["progress"], percent),
                           stage=stage, message=f"Receiver recovery: {stage}. Keep USB connected.")
                self._save_progress(job)

        with self.platform.wait_for_boot(device, self.state_dir / "locks") as boot:
            if boot.usb_pid != 0x3208:
                raise ValueError("Unexpected receiver recovery USB personality.")
            flash_boot_receiver(boot.transport, image, identity=original, boot_usb_pid=boot.usb_pid,
                                qualification=authorization, progress=progress)
        job.update(state="verifying", progress=95, recovery_confirmed=False,
                   message="Cached firmware reinstalled. Verifying the same receiver's target version.")
        self._save_progress(job)
        try:
            _, identity = self.platform.wait_for_application(
                device, self.state_dir / "locks", expected_product_id=0x6008,
                expected_version_raw=image.version_raw & 0xFFFF,
            )
        except (OSError, ValueError, RuntimeError):
            job.update(state="blocked", recovery_ready=False, can_install=False,
                       message="Recovery transfer finished, but the target firmware version was not confirmed. Check recovery again; no update was retried.")
        else:
            self._record_recovery_version(job, identity)
        self._save_progress(job)

    def _save_progress(self, job: dict) -> None:
        try:
            self.store.save(job)
        except (OSError, ValueError):
            job["reporting_error"] = "Progress storage failed; verify the device before retrying."

    def export_diagnostics(self) -> dict:
        devices, warnings = self._devices(probe=False)
        payload = {
            "schema_version": 1, "app_version": __version__,
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
        return self.platform.open_official_updater(OFFICIAL_UPDATER)
