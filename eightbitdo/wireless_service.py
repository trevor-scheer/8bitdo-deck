"""Wireless jobs share the physical receiver lock with its own USB update jobs."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time

from .discovery import Device, clean_text
from .protocol import FirmwareMismatch, parse_firmware
from .qualifications import wireless_qualification_for
from .wireless import flash_wireless_controller, query_wireless_target, verify_wireless_sources


def receiver_id(paired_id: str) -> str:
    if not isinstance(paired_id, str) or not re.fullmatch(r"paired-[0-9a-f]{16}", paired_id):
        raise ValueError("Invalid wireless controller selection.")
    return "usb-" + paired_id.removeprefix("paired-")


def open_receiver(service, receiver):
    return service.platform.open_hid(
        receiver.hid_paths[0], int(receiver.pid, 16),
        service.state_dir / "locks" / (receiver.id + ".lock"), expected_usb_path=receiver.usb_path,
    )


def paired_devices(service, devices: list[Device]) -> list[Device]:
    result = []
    for receiver in devices:
        if not receiver.paired_firmware_version:
            continue
        paired = Device(
            id="paired-" + receiver.id.removeprefix("usb-"),
            name="Ultimate 2 Wireless controller (via receiver)", vid="2dc8", pid="wireless",
            role="controller", model_id="ultimate2-controller", firmware_version=receiver.paired_firmware_version,
            backend="wireless", status="wireless", can_update=False,
            reason="Controller status is available. Installation requires a reviewed 2.4 GHz receiver and controller identity.",
        )
        if service.platform.native_write and receiver.status == "identified" and receiver.hid_paths:
            try:
                with open_receiver(service, receiver) as transport:
                    target = query_wireless_target(transport, usb_pid=int(receiver.pid, 16))
                paired.status = "identified"
                paired.firmware_version = target.controller.version
                paired.firmware_pid = target.controller.pid
                paired.reason = "Controller identified through its 2.4 GHz receiver. Preparation checks the exact firmware and recovery support before installation."
            except (OSError, ValueError, TimeoutError, RuntimeError) as error:
                paired.reason = clean_text(str(error))
        result.append(paired)
    return result


def require_receiver(service, paired_id: str) -> Device:
    receiver = service._native_device(receiver_id(paired_id), "ultimate2-receiver")
    if not receiver.usb_path or not Path(receiver.usb_path).is_absolute():
        raise ValueError("The wireless receiver's physical USB port is unavailable.")
    return receiver


def prepare_wireless(service, job: dict, image) -> None:
    job["backend"] = "wireless"
    receiver = require_receiver(service, job["device_id"])
    with open_receiver(service, receiver) as transport:
        target = query_wireless_target(transport, usb_pid=int(receiver.pid, 16))
        if target.controller.version_raw == image.version_raw:
            job["message"] = f"This controller already has the selected firmware {image.version}. No update is needed."
            return
        authorization = wireless_qualification_for(image, target)
        if authorization is None:
            job["message"] = f"Wireless installation from {target.controller.version} to {image.version} is not supported for this receiver, controller, and firmware image."
            return
        job.update(evidence_level=authorization.controller.evidence_level, evidence=authorization.controller.evidence)
        if not service.recovery_access.status()["configured"]:
            job["message"] = "Set up USB recovery access before preparing a wireless controller update."
            return
        target = verify_wireless_sources(transport, image, usb_pid=int(receiver.pid, 16), authorization=authorization)
    job.update(
        state="ready", can_install=True, receiver_id=receiver.id, original_usb_path=receiver.usb_path,
        original_version=target.controller.version, original_version_raw=target.controller.version_raw,
        original_hardware_revision=target.controller.hardware_revision,
        receiver_version_raw=target.receiver.version_raw, controller_edition=target.status.edition,
        message="Wireless firmware and device checks passed. Keep the receiver connected by USB and the controller powered on in 2.4 GHz mode. Review the experimental evidence before installing.",
    )


def cached_image(service, job: dict):
    cache = (service.state_dir / "firmware").resolve()
    path = Path(job["artifact_path"])
    if path.is_symlink() or not path.resolve().is_relative_to(cache):
        raise ValueError("The firmware image is outside the validated cache.")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != job["sha256"]:
        raise ValueError("The firmware image changed after preparation.")
    image = parse_firmware(data, expected_pid=0x6012)
    if image.version != job["version"]:
        raise ValueError("The firmware version changed after preparation.")
    return image


def wait_for_wireless_application(service, original: Device, target, image, *, timeout: float = 30.0):
    if not 0 < timeout <= 60:
        raise ValueError("Wireless reconnection timeout must be between zero and 60 seconds.")
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        receiver = next((device for device in service.platform.discover()
                         if device.id == original.id and device.usb_path == original.usb_path), None)
        if receiver and receiver.hid_paths and receiver.pid in {"310b", "6013"}:
            try:
                with open_receiver(service, receiver) as transport:
                    returned = query_wireless_target(transport, usb_pid=int(receiver.pid, 16),
                                                     timeout=min(1.0, max(0.01, (deadline - time.monotonic()) / 5)))
            except (OSError, ValueError, TimeoutError, RuntimeError) as error:
                last_error = error
            else:
                if returned.receiver != target.receiver or returned.status.edition != target.status.edition:
                    raise FirmwareMismatch("The wireless receiver or controller edition changed during reconnection.")
                if returned.controller.pid == image.pid and returned.controller.version_raw == image.version_raw and not returned.controller.beta:
                    return returned
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise TimeoutError("The selected receiver's paired controller did not report the expected firmware after reset.") from last_error


def install_wireless(service, job: dict) -> None:
    if job.get("model_id") != "ultimate2-controller" or job.get("receiver_id") != receiver_id(job["device_id"]):
        raise ValueError("Wireless job does not identify its original receiver and controller.")
    image = cached_image(service, job)
    if not service.recovery_access.status()["configured"]:
        raise ValueError("Configure USB recovery access before installing wireless firmware.")
    receiver = require_receiver(service, job["device_id"])
    if receiver.usb_path != job.get("original_usb_path"):
        raise FirmwareMismatch("The wireless receiver moved from its prepared USB port.")
    with open_receiver(service, receiver) as transport:
        target = query_wireless_target(transport, usb_pid=int(receiver.pid, 16))
        authorization = wireless_qualification_for(image, target)
        if (authorization is None or target.controller.version != job.get("original_version")
                or target.controller.version_raw != job.get("original_version_raw")
                or target.controller.hardware_revision != job.get("original_hardware_revision")
                or target.receiver.version_raw != job.get("receiver_version_raw")
                or target.status.edition != job.get("controller_edition")
                or authorization.controller.evidence_level != job.get("evidence_level")
                or authorization.controller.evidence != job.get("evidence")):
            raise FirmwareMismatch("The wireless target or authorization changed. Prepare and review the update again.")
        job.update(state="installing", progress=0, can_install=False, recovery_ready=False,
                   wireless_install_attempted=True,
                   message="Installing controller firmware over 2.4 GHz. Keep the controller powered on and the receiver connected by USB.")
        # Source identity and receiver port were persisted by preparation; this
        # durable transition also precedes initialization and every flash write.
        service.store.save(job)

        def progress(stage, done, total):
            percent = min(90, int(done * 90 / max(1, total)))
            if percent > job["progress"] or job.get("stage") != stage:
                job.update(progress=max(job["progress"], percent), stage=stage,
                           message=f"Wireless update: {stage}. Keep the controller powered on and the receiver connected.")
                service._save_progress(job)

        flash_wireless_controller(transport, image, usb_pid=int(receiver.pid, 16), authorization=authorization,
                                  expected_target=target, progress=progress)
    job.update(state="verifying", progress=95, message="Waiting for the controller to reconnect through the same receiver and checking its firmware version.")
    service._save_progress(job)
    try:
        wait_for_wireless_application(service, receiver, target, image)
    except (OSError, ValueError, TimeoutError, RuntimeError):
        job.update(state="blocked", can_install=False,
                   message="The image verified, but the controller's new firmware could not be confirmed. Keep the receiver connected and check controller recovery before retrying.")
    else:
        job.update(state="complete", progress=100, message=f"Wireless update complete. The controller reports firmware {image.version}.")
    service._save_progress(job)
