"""Explicit USB recovery for a durably recorded interrupted wireless install.

The service/worker owns the job lock and suspend inhibitor. The controller's
recovery USB port is discovered separately from its radio receiver's USB port.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import nullcontext
from pathlib import Path

from .controller_recovery import (
    DamagedControllerMetadata, finish_controller_recovery, flash_controller_recovery,
    inspect_controller_recovery, inspect_controller_rescue, query_controller_boot_target,
)
from .discovery import clean_text
from .protocol import DeviceIdentity, FirmwareMismatch, PairedControllerInfo, query_identity
from .qualifications import wireless_qualification_for
from .wireless import WirelessAuthorization, WirelessTarget
from .wireless_service import cached_image, receiver_id


ACTIONS = frozenset({"reflash", "finish-pending", "return-installed"})


def _port_identity(port) -> str:
    if (not isinstance(port, str) or not port or not Path(port).is_absolute()
            or os.path.normpath(port) != port or ".." in Path(port).parts):
        raise ValueError("A canonical physical USB port is required for controller recovery.")
    return "usb-" + hashlib.sha256(port.encode()).hexdigest()[:16]


def recovery_context(service, job: dict, *, queued: bool = False):
    """Validate durable source context/cache/compiled policy without device I/O."""
    states = {"queued"} if queued else {"failed", "blocked"}
    if (job.get("state") not in states or job.get("backend") != "wireless"
            or job.get("model_id") != "ultimate2-controller"
            or job.get("worker_action") not in {"install", "recover"}
            or job.get("wireless_install_attempted") is not True
            or job.get("evidence_level") != "source-reviewed-experimental"
            or type(job.get("original_version_raw")) is not int
            or not 0 < job["original_version_raw"] <= 0xFFFF
            or type(job.get("receiver_version_raw")) is not int
            or type(job.get("controller_edition")) is not int):
        raise ValueError("This job has no recorded attempted wireless controller install to recover.")
    original_receiver = _port_identity(job.get("original_usb_path"))
    if job.get("receiver_id") != original_receiver or receiver_id(job.get("device_id")) != original_receiver:
        raise ValueError("The wireless recovery job does not match its original receiver port.")
    original = DeviceIdentity(0x6012, job["original_version_raw"], hardware_revision=job.get("original_hardware_revision"))
    if job.get("original_version") != original.version:
        raise ValueError("The original controller version record is inconsistent.")
    image = cached_image(service, job)
    target = WirelessTarget(
        DeviceIdentity(0x6013, job["receiver_version_raw"]), original,
        PairedControllerInfo(original.version_raw, 0, None, job["controller_edition"]),
    )
    authorization = wireless_qualification_for(image, target)
    if (type(authorization) is not WirelessAuthorization
            or authorization.controller.evidence_level != job.get("evidence_level")
            or authorization.controller.evidence != job.get("evidence")):
        raise ValueError("The compiled wireless recovery authorization changed or is unavailable.")
    if job.get("recovery_usb_path") is not None or job.get("recovery_device_id") is not None:
        if _port_identity(job.get("recovery_usb_path")) != job.get("recovery_device_id"):
            raise ValueError("The recorded controller USB recovery port is inconsistent.")
    return image, authorization


def _require_access(service):
    service.platform.require_write_support()
    if not service.recovery_access.status()["configured"]:
        raise ValueError("Configure USB recovery access before checking controller recovery.")


def _validate_usb_device(device):
    if (device.backend != "hid" or device.vid != "2dc8"
            or _port_identity(device.usb_path) != device.id):
        raise ValueError("The controller recovery USB identity is inconsistent.")


def _recorded_device(service, job):
    expected = job.get("recovery_device_id")
    if not expected or _port_identity(job.get("recovery_usb_path")) != expected:
        raise ValueError("Check and record the controller USB recovery port before confirming recovery.")
    matches = [device for device in service.platform.discover()
               if device.id == expected and device.usb_path == job["recovery_usb_path"]]
    if len(matches) != 1 or matches[0].pid not in {"3208", "6012", "310b"}:
        raise ValueError("Connect the same controller on its recorded USB recovery port.")
    _validate_usb_device(matches[0])
    return matches[0]


def _discover_controller_boot(service, job):
    candidates = [device for device in service.platform.discover()
                  if device.vid == "2dc8" and device.backend == "hid" and device.pid == "3208"]
    if not candidates or len(candidates) > 8:
        raise ValueError("Connect only the selected controller by USB in recovery mode, then check recovery again.")
    matches = []
    for device in candidates:
        _validate_usb_device(device)
        with (nullcontext() if device.id == job["receiver_id"] else service.store.lock("update-" + device.id)):
            with service.platform.wait_for_boot(device, service.state_dir / "locks", timeout=2.0) as boot:
                if boot.usb_pid != 0x3208:
                    raise ValueError("Unexpected USB recovery personality.")
                # This shared, source-reviewed boot query only asks for PID.
                # Do not initialize or read controller memory on a receiver.
                if query_controller_boot_target(boot.transport) == 0x6012:
                    matches.append(device)
    if len(matches) != 1:
        raise ValueError("Controller recovery is ambiguous. Connect only the selected controller by USB in recovery mode.")
    device = matches[0]
    return device


def _inspect(transport, image, authorization):
    try:
        status = inspect_controller_recovery(transport, image, authorization=authorization.controller)
    except DamagedControllerMetadata:
        status = inspect_controller_rescue(transport, image, authorization=authorization.controller)
    else:
        if status.action == "blocked":
            status = inspect_controller_rescue(transport, image, authorization=authorization.controller)
    expected = image.version_raw & 0xFFFF if status.action in {"reflash", "finish-pending"} else getattr(status, "expected_version_raw", None)
    return status, expected


def _runtime_identity(service, device, job):
    if device.pid not in {"6012", "310b"} or not device.hid_paths:
        raise ValueError("The controller has not returned to its reviewed USB application interface.")
    with service.platform.open_hid(device.hid_paths[0], int(device.pid, 16),
                                   service.state_dir / "locks" / (device.id + ".lock"),
                                   expected_usb_path=job["recovery_usb_path"]) as transport:
        return query_identity(transport, usb_pid=int(device.pid, 16))


def _record_version(job, identity):
    if (not identity.target_verified or identity.pid != 0x6012 or identity.beta
            or identity.hardware_revision != job.get("original_hardware_revision")):
        raise FirmwareMismatch("The returning USB device is not the reviewed stable controller.")
    job.update(can_install=False, recovery_ready=False, recovery_confirmed=False, progress=100)
    if identity.version == job["version"]:
        job.update(state="complete", recovery_action="target-confirmed",
                   message=f"The USB controller reports firmware {identity.version}. The target version is verified.")
    elif identity.version_raw == job["original_version_raw"]:
        job.update(state="blocked", recovery_action="source-unchanged",
                   message=f"The controller returned to its original firmware {identity.version}; the update was not completed.")
    else:
        raise FirmwareMismatch("The returning controller reports an unexpected firmware version.")


def check_recovery(service, job: dict) -> None:
    """Caller owns the job lock; inspect only, never enter boot mode or reset."""
    image, authorization = recovery_context(service, job)
    job.update(recovery_ready=False, recovery_action=None, recovery_confirmed=False, can_install=False)
    service.store.save(job)
    _require_access(service)
    with service.store.lock("update-" + job["receiver_id"]):
        device = (_recorded_device(service, job) if job.get("recovery_device_id")
                  else _discover_controller_boot(service, job))
        with (nullcontext() if device.id == job["receiver_id"] else service.store.lock("update-" + device.id)):
            if device.pid in {"6012", "310b"}:
                _record_version(job, _runtime_identity(service, device, job))
            else:
                with service.platform.wait_for_boot(device, service.state_dir / "locks", timeout=2.0) as boot:
                    if boot.usb_pid != 0x3208:
                        raise ValueError("Unexpected controller boot USB personality.")
                    status, expected = _inspect(boot.transport, image, authorization)
                ready = status.action in ACTIONS and expected in {job["original_version_raw"], image.version_raw & 0xFFFF}
                job.update(state="blocked", recovery_ready=ready, recovery_action=status.action,
                           message=status.reason + ". Only connect the controller from this failed update; "
                           "the first USB connection cannot distinguish identical controllers. "
                           "No firmware was written or reset sent.")
                if ready:
                    job.update(recovery_usb_path=device.usb_path, recovery_device_id=device.id,
                               recovery_expected_version_raw=expected)
    service.store.save(job)


def run_recover(service, job: dict) -> None:
    """Protected worker; repeat policy, cache and same-controller checks before mutation."""
    if job.get("worker_action") != "recover" or job.get("recovery_confirmed") is not True:
        raise ValueError("This job has no confirmed controller recovery request.")
    image, authorization = recovery_context(service, job, queued=True)
    action = job.get("recovery_action")
    expected = job.get("recovery_expected_version_raw")
    if action not in ACTIONS or expected not in {job["original_version_raw"], image.version_raw & 0xFFFF}:
        raise ValueError("Run a successful controller recovery check before confirming recovery.")
    _require_access(service)
    with service.store.lock("update-" + job["receiver_id"]):
        device = _recorded_device(service, job)
        with (nullcontext() if device.id == job["receiver_id"] else service.store.lock("update-" + device.id)):
            if device.pid in {"6012", "310b"}:
                _record_version(job, _runtime_identity(service, device, job))
                service.store.save(job)
                return
            job.update(state="verifying", recovery_ready=False, can_install=False,
                       message="Rechecking the recorded controller USB boot interface and cached firmware before recovery.")
            service.store.save(job)
            try:
                with service.platform.wait_for_boot(device, service.state_dir / "locks", timeout=2.0) as boot:
                    if boot.usb_pid != 0x3208:
                        raise ValueError("Unexpected controller boot USB personality.")
                    current, current_expected = _inspect(boot.transport, image, authorization)
                    if current.action != action or current_expected != expected:
                        raise FirmwareMismatch("Controller recovery state changed; inspect and confirm recovery again.")
                    job.update(state="installing" if action == "reflash" else "verifying",
                               recovery_confirmed=False, progress=0,
                               message="Performing the confirmed controller USB recovery. Keep USB connected.")
                    # This durable write must succeed before any erase or reset.
                    service.store.save(job)
                    if action == "reflash":
                        flash_controller_recovery(boot.transport, image, authorization=authorization.controller, confirmed=True)
                    else:
                        finish_controller_recovery(boot.transport, image, authorization=authorization.controller)
                job.update(state="verifying", progress=95,
                           message="Controller recovery reset sent. Verifying its firmware on the same USB port.")
                service._save_progress(job)
                _, identity = service.platform.wait_for_application(
                    device, service.state_dir / "locks", expected_product_id=0x6012, expected_version_raw=expected,
                )
                if identity.version_raw != expected:
                    raise FirmwareMismatch("The controller did not return to the expected recovery version.")
                _record_version(job, identity)
            except (OSError, ValueError, RuntimeError) as error:
                job.update(state="blocked", recovery_ready=False, recovery_confirmed=False, can_install=False,
                           message="Controller USB recovery stopped: " + clean_text(str(error), 900)
                           + ". No erase, write, or reset was retried. Check recovery again.")
            service._save_progress(job)
