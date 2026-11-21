import copy
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from eightbitdo.fwupd import Fwupd, FwupdError


DEVICE_ID = "a" * 40
PUBLIC_ID = "fwupd-" + DEVICE_ID


def device(**changes):
    value = {
        "DeviceId": DEVICE_ID,
        "Plugin": "ebitdo",
        "Name": "8BitDo Controller",
        "Version": "1.01",
        "VendorIds": ["USB:0x0483"],
        "Flags": ["updatable", "supported", "is-bootloader"],
    }
    value.update(changes)
    return value


def release(**changes):
    value = {
        "Version": "1.02",
        "RemoteId": "lvfs",
        "AppstreamId": "com.8bitdo.controller.firmware",
        "ReleaseId": "42",
        "Checksum": ["b" * 64],
        "Created": 1735689600,
        "Description": "<p>Improves connection stability.</p>",
        "Flags": ["trusted-metadata", "is-upgrade"],
    }
    value.update(changes)
    return value


class FakeFwupd(Fwupd):
    def __init__(self, devices=None, releases=None):
        super().__init__("/usr/bin/fwupdmgr")
        self.device_rows = devices if devices is not None else [device()]
        self.release_rows = releases if releases is not None else [release()]
        self.queries = []

    def _json(self, *args):
        self.queries.append(args)
        if args[0] == "get-devices":
            return {"Devices": copy.deepcopy(self.device_rows)}
        if args[0] == "get-releases":
            return {"Releases": copy.deepcopy(self.release_rows)}
        raise AssertionError(args)


class FwupdTests(unittest.TestCase):
    def test_only_real_ebitdo_plugin_devices_are_exposed(self):
        backend = FakeFwupd(devices=[
            device(), device(DeviceId="b" * 40, Plugin="uefi_capsule"),
            device(DeviceId="../unsafe"), device(DeviceId=123), "bad",
        ])
        devices = backend.devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].id, PUBLIC_ID)
        self.assertEqual(devices[0].vid, "0483")
        self.assertTrue(devices[0].can_update)

    def test_runtime_device_requires_manual_bootloader_before_install(self):
        backend = FakeFwupd(devices=[device(Flags=["updatable", "supported"], UpdateMessage="Reconnect in bootloader mode.")])
        found = backend.devices()[0]
        self.assertFalse(found.can_update)
        self.assertEqual(found.reason, "Reconnect in bootloader mode.")
        with self.assertRaises(FwupdError):
            backend.require_device(PUBLIC_ID)

    def test_device_blockers_are_not_overridden(self):
        for flag in ("locked", "updatable-hidden", "wildcard-install", "emulated", "historical", "needs-reboot", "install-all-releases"):
            with self.subTest(flag=flag):
                backend = FakeFwupd(devices=[device(Flags=["updatable", "supported", "is-bootloader", flag])])
                self.assertFalse(backend.devices()[0].can_update)
        self.assertFalse(FakeFwupd(devices=[device(Problems=["power-too-low"])]).devices()[0].can_update)
        self.assertFalse(FakeFwupd(devices=[device(Flags="updatable supported is-bootloader")]).devices()[0].can_update)

    def test_device_ids_cannot_be_commands_paths_or_system_targets(self):
        backend = FakeFwupd()
        for selection in ("", "*", "--force", "../../file.cab", DEVICE_ID, "fwupd-" + "c" * 40, None):
            with self.subTest(selection=selection), self.assertRaises(FwupdError):
                backend.require_device(selection)

    def test_release_fingerprint_uses_device_and_payload_not_presentation(self):
        backend = FakeFwupd()
        before = backend.releases(PUBLIC_ID)[0]
        self.assertTrue(before["downloadable"])
        self.assertEqual(before["date"], "2025-01-01")
        backend.release_rows[0].update(Created=1735776000, Description="Changed notes", Reports=[{"random": 1}])
        self.assertEqual(before["id"], backend.releases(PUBLIC_ID)[0]["id"])
        backend.release_rows[0]["Checksum"] = ["c" * 64]
        self.assertNotEqual(before["id"], backend.releases(PUBLIC_ID)[0]["id"])
        backend.release_rows[0]["Checksum"] = ["b" * 64]
        other = "fwupd-" + "d" * 40
        backend.device_rows = [device(DeviceId="d" * 40)]
        self.assertNotEqual(before["id"], backend.releases(other)[0]["id"])

    def test_release_flags_require_trusted_eligible_upgrade(self):
        for flags in (
            [], ["trusted-payload", "is-upgrade"], ["trusted-metadata"],
            "trusted-metadata is-upgrade",
            *(["trusted-metadata", "is-upgrade", flag] for flag in (
                "is-downgrade", "blocked-version", "blocked-approval", "is-alternate-branch", "is-local",
            )),
        ):
            with self.subTest(flags=flags):
                row = FakeFwupd(releases=[release(Flags=flags)]).releases(PUBLIC_ID)[0]
                self.assertFalse(row["downloadable"])

    def test_missing_payload_identity_or_wrong_protocol_blocks_release(self):
        for changes in (
            {"Checksum": []}, {"Checksum": "b" * 64}, {"Checksum": ["bad"]},
            {"RemoteId": None}, {"RemoteId": "../remote"}, {"Protocol": "org.uefi.capsule"},
            {"AppstreamId": {"bad": "type"}},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(FakeFwupd(releases=[release(**changes)]).releases(PUBLIC_ID)[0]["downloadable"])

    def test_ambiguous_equal_versions_cannot_select_wrong_payload(self):
        backend = FakeFwupd(releases=[release(), release(Version="1.2", Checksum=["c" * 64])])
        rows = backend.releases(PUBLIC_ID)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not row["downloadable"] for row in rows))
        self.assertIn("Multiple releases", rows[0]["verification"])

    def test_invalid_versions_and_malformed_lists_fail_safely(self):
        backend = FakeFwupd(releases=[release(Version="--force"), release(Version="https://evil.test/a"), release(Version=3)])
        self.assertEqual(backend.releases(PUBLIC_ID), [])
        with self.assertRaises(FwupdError):
            FakeFwupd(devices={}).devices()
        with self.assertRaises(FwupdError):
            FakeFwupd(releases={}).releases(PUBLIC_ID)

    @patch("eightbitdo.fwupd.shutil.which", return_value="/usr/bin/systemd-inhibit")
    @patch("eightbitdo.fwupd.subprocess.run")
    def test_install_is_targeted_inhibited_and_never_timed_out(self, run, which):
        backend = FakeFwupd()
        row = backend.releases(PUBLIC_ID)[0]

        def installed(command, **options):
            self.assertEqual(command[-3:], ["install", DEVICE_ID, "1.02"])
            self.assertEqual(command[0], "/usr/bin/systemd-inhibit")
            self.assertIn("--what=sleep", command)
            self.assertIn("--mode=block", command)
            self.assertNotIn("timeout", options)
            self.assertNotIn("shell", options)
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(list(Path(options["cwd"]).iterdir()), [])
            for unsafe in ("--force", "--allow-older", "--allow-reinstall", "--allow-branch-switch", "--disable-ssl-strict", "--no-safety-check", "update", "local-install"):
                self.assertNotIn(unsafe, command)
            return subprocess.CompletedProcess(command, 0, "", "")

        run.side_effect = installed
        backend.install(PUBLIC_ID, row["id"], row["version"])
        run.assert_called_once()

    @patch("eightbitdo.fwupd.subprocess.run")
    def test_changed_release_trust_or_device_never_starts_install(self, run):
        for change in ("checksum", "trust", "device", "version"):
            backend = FakeFwupd()
            row = backend.releases(PUBLIC_ID)[0]
            if change == "checksum":
                backend.release_rows[0]["Checksum"] = ["c" * 64]
            elif change == "trust":
                backend.release_rows[0]["Flags"] = ["is-upgrade"]
            elif change == "device":
                backend.device_rows[0]["Plugin"] = "uefi_capsule"
            else:
                row["version"] = "1.03"
            with self.subTest(change=change), self.assertRaises(FwupdError):
                backend.install(PUBLIC_ID, row["id"], row["version"])
        run.assert_not_called()

    @patch("eightbitdo.fwupd.shutil.which", return_value=None)
    @patch("eightbitdo.fwupd.subprocess.run")
    def test_missing_inhibitor_blocks_install(self, run, which):
        backend = FakeFwupd()
        row = backend.releases(PUBLIC_ID)[0]
        with self.assertRaisesRegex(FwupdError, "prevent suspend"):
            backend.install(PUBLIC_ID, row["id"], row["version"])
        run.assert_not_called()

    @patch("eightbitdo.fwupd.subprocess.run")
    def test_reads_have_timeout_and_errors_are_normalized(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"Devices": []}), "")
        self.assertEqual(Fwupd("/usr/bin/fwupdmgr").devices(), [])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        run.side_effect = subprocess.TimeoutExpired("fwupdmgr", 30)
        with self.assertRaisesRegex(FwupdError, "30 seconds"):
            Fwupd("/usr/bin/fwupdmgr").devices()
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 1, json.dumps({"Error": {"Message": 7}}), "")
        with self.assertRaises(FwupdError):
            Fwupd("/usr/bin/fwupdmgr").devices()


if __name__ == "__main__":
    unittest.main()
