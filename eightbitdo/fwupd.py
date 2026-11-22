import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone

from .discovery import Device, clean_text
from .runtime import external_environment


class FwupdError(RuntimeError):
    pass


def _flags(value) -> set[str]:
    return set(value) if isinstance(value, list) and all(isinstance(flag, str) for flag in value) else set()


def _text(value, fallback: str = "", limit: int = 160) -> str:
    return clean_text(value, limit) if isinstance(value, str) else fallback


def _version_key(version: str) -> tuple[int, ...] | None:
    # The ebitdo plugin uses numeric pair versions. Normalize equivalent spellings
    # because fwupdmgr selects a release by version comparison, not release ID.
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,5}\.[0-9]{1,5}", version):
        return None
    return tuple(int(part) for part in version.split("."))


def _release_identity(device_id: str, item: dict) -> dict | None:
    checksums = item.get("Checksum")
    if not isinstance(checksums, list) or not checksums:
        return None
    if any(not isinstance(checksum, str) or not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64}|[a-fA-F0-9]{128})", checksum) for checksum in checksums):
        return None
    remote = item.get("RemoteId")
    if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", remote):
        return None
    if item.get("Protocol") not in (None, "com.8bitdo"):
        return None
    fields = ("AppstreamId", "ReleaseId", "Branch")
    if any(item.get(key) is not None and not isinstance(item[key], str) for key in fields):
        return None
    return {
        "device": device_id, "version": item["Version"], "remote": remote,
        "checksums": sorted({checksum.lower() for checksum in checksums}),
        **{key: item.get(key) for key in fields},
    }


class Fwupd:
    def __init__(self, executable: str | None = None):
        self.executable = executable or shutil.which("fwupdmgr")

    def _json(self, *args: str) -> dict:
        if not self.executable:
            raise FwupdError("fwupd is not installed. Vendor firmware browsing remains available.")
        try:
            result = subprocess.run(
                [self.executable, "--json", "--no-unreported-check", "--no-remote-check", *args],
                capture_output=True, text=True, timeout=30, env=external_environment(),
            )
            value = json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            raise FwupdError("fwupd did not respond within 30 seconds.") from None
        except (OSError, ValueError):
            raise FwupdError("Could not read a JSON response from fwupd.") from None
        if not isinstance(value, dict):
            raise FwupdError("Invalid fwupd response.")
        if result.returncode or "Error" in value:
            error = value.get("Error", {})
            message = error.get("Message", "fwupd could not complete the request.") if isinstance(error, dict) else str(error)
            raise FwupdError(_text(message, "fwupd could not complete the request.", 400))
        return value

    def devices(self) -> list[Device]:
        result = []
        rows = self._json("get-devices").get("Devices", [])
        if not isinstance(rows, list):
            raise FwupdError("Invalid fwupd device list.")
        for item in rows:
            # Restrict this app to fwupd's 8BitDo plugin, never system firmware.
            if not isinstance(item, dict) or item.get("Plugin") != "ebitdo":
                continue
            device_id = item.get("DeviceId", "")
            if not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f]{40}", device_id):
                continue
            flags = _flags(item.get("Flags"))
            blockers = {"locked", "updatable-hidden", "wildcard-install", "emulated", "historical", "needs-reboot", "needs-shutdown", "needs-activation", "install-all-releases"}
            # ebitdo detach() always requests a manual replug outside bootloader
            # mode, even without the needs-bootloader flag. Our background CLI
            # cannot present those interactive requests, so require it beforehand.
            updatable = {"updatable", "supported", "is-bootloader"} <= flags and not flags & blockers and not item.get("Problems")
            if "is-bootloader" not in flags:
                reason = _text(item.get("UpdateMessage"), limit=1000) or "Enter this controller's bootloader using its model-specific manual, reconnect USB, and scan again."
            elif not updatable:
                reason = "fwupd recognizes this device, but it is not ready for a supported update."
            else:
                reason = "Bootloader ready. Trusted upgrade releases are verified and installed by fwupd."
            vendor_ids = item.get("VendorIds", [])
            vid = next((value[6:].lower() for value in vendor_ids if isinstance(value, str) and re.fullmatch(r"USB:0x[0-9A-Fa-f]{4}", value)), "fwupd") if isinstance(vendor_ids, list) else "fwupd"
            result.append(Device(
                id="fwupd-" + device_id, name=_text(item.get("Name"), "8BitDo device"),
                vid=vid, pid="fwupd", role="device", model_id="fwupd-" + device_id,
                firmware_version=_text(item.get("Version")) or None,
                status="supported" if updatable else "connection_required", can_update=updatable,
                reason=reason,
                backend="fwupd",
            ))
        return result

    def require_device(self, device_id: str) -> Device:
        if not isinstance(device_id, str) or not re.fullmatch(r"fwupd-[0-9a-f]{40}", device_id):
            raise FwupdError("Invalid 8BitDo fwupd device ID.")
        device = next((d for d in self.devices() if d.id == device_id), None)
        if not device or not device.can_update:
            raise FwupdError("That 8BitDo device is no longer available for updating. Scan again.")
        return device

    def releases(self, device_id: str) -> list[dict]:
        self.require_device(device_id)
        rows = self._json("get-releases", device_id.removeprefix("fwupd-")).get("Releases", [])
        if not isinstance(rows, list):
            raise FwupdError("Invalid fwupd release list.")
        versions = Counter(_version_key(item.get("Version")) for item in rows if isinstance(item, dict))
        releases = []
        for item in rows:
            version = item.get("Version") if isinstance(item, dict) else None
            if _version_key(version) is None:
                continue
            flags = _flags(item.get("Flags"))
            identity = _release_identity(device_id, item)
            blocked = flags & {"is-downgrade", "is-alternate-branch", "blocked-version", "blocked-approval", "is-local"}
            trusted = {"trusted-metadata", "is-upgrade"} <= flags and not blocked and identity is not None
            if versions[_version_key(version)] > 1:
                trusted = False
                verification = "Multiple releases share this version; exact release selection is unavailable."
            elif trusted:
                verification = "fwupd trusted metadata and an eligible upgrade release."
            else:
                verification = "No eligible, uniquely identified upgrade with trusted fwupd metadata; installation blocked."
            # Notes, dates and report counts can change without changing the image.
            # Trust flags are rechecked independently on each install request.
            fingerprint = hashlib.sha256(json.dumps(identity or {"device": device_id, "version": version}, sort_keys=True).encode()).hexdigest()
            created = item.get("Created")
            try:
                released_at = datetime.fromtimestamp(created, timezone.utc).date().isoformat() if type(created) is int and created > 0 else ""
            except (OSError, OverflowError, ValueError):
                released_at = ""
            releases.append({
                "id": fingerprint, "version": version, "date": released_at,
                "notes": clean_text(re.sub(r"<[^>]+>", " ", _text(item.get("Description"), limit=6000)), 6000),
                "downloadable": trusted,
                "verification": verification,
            })
        return releases

    def install(self, device_id: str, release_id: str, expected_version: str) -> None:
        if not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id) or _version_key(expected_version) is None:
            raise FwupdError("Invalid firmware release selection.")
        self.require_device(device_id)
        release = next((r for r in self.releases(device_id) if r["id"] == release_id), None)
        if not release or not release["downloadable"] or release["version"] != expected_version:
            raise FwupdError("The selected trusted release changed. Prepare the update again.")
        inhibitor = shutil.which("systemd-inhibit")
        if not inhibitor:
            raise FwupdError("Cannot prevent suspend during the update: systemd-inhibit is unavailable.")
        # No force, downgrade, reinstall, SSL bypass, or system-wide update switches.
        # fwupdmgr install falls back to local-install if the device argument is
        # also an existing relative filename. An empty working directory removes
        # that ambiguity. Once flashing begins, never kill it on a client timeout.
        try:
            with tempfile.TemporaryDirectory(prefix="eightbitdo-fwupd-") as directory:
                result = subprocess.run([
                    inhibitor, "--what=sleep", "--mode=block", "--who=8BitDo Deck",
                    "--why=Updating controller firmware", self.executable,
                    "--json", "--assume-yes", "--no-unreported-check", "--no-remote-check", "--no-reboot-check",
                    "install", device_id.removeprefix("fwupd-"), expected_version,
                ], capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=directory, env=external_environment())
        except OSError as error:
            raise FwupdError("Could not start the firmware updater.") from error
        if result.returncode:
            raise FwupdError("fwupd could not finish the update. Check device connection and system authorization; do not retry blindly.")
