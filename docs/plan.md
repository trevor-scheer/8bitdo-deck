# Implementation plan

Status on 2026-11-21: the Decky plugin, Python CLI, catalog, job engine, fwupd integration, and native transaction backends are implemented. **One source-reviewed experimental update is available after matching preflight:** Ultimate 2 Wireless receiver stable 1.01 to the exact official 1.06 image. No native update is hardware-qualified; controller, wireless, and legacy native writes remain blocked. No firmware image erase, write, or commit has been sent during validation.

## Product scope

Provide one Steam Deck interface that identifies controllers and receivers automatically, selects the matching official firmware, and explains connection requirements. Start with the owned Ultimate Bluetooth and Ultimate 2 Wireless controller/receiver pairs. Extend support to other controllers and accessories through explicit model and protocol evidence; unknown devices stay visible without an installation claim.

The Decky plugin uses a reusable Python engine and CLI. A standalone app can reuse that engine later. The official web updater remains an optional launcher, not a verified update route for every cataloged model.

## Implemented

- USB/HID discovery, verified identity and version queries, separate controller/receiver entries, and sanitized diagnostics. The original Bluetooth receiver is identified by its model tag; manual selection cannot override a mismatch.
- Official catalogs, bounded downloads, image structure and target checks, and local SHA-256 receipts. Local hashes bind bytes but do not prove vendor authenticity.
- Background jobs, per-target locks, suspend inhibition, persistent progress, targeted legacy fwupd installation, and reconnect/version checks.
- Ultimate 2 application transactions with metadata guards, source fingerprint checks for experimental authorization, per-block verification, and a second complete verification pass before commit.
- A separate original Bluetooth receiver transaction integrated into the service, with authorization checks before boot entry, descriptor and firmware identity checks, and handoff on the same physical USB port. See the [legacy protocol](legacy-receiver-protocol.md).
- Automatic recognition of SteamOS’s trusted recovery access policy, plus actual interface access checks. Other Linux systems can use the scoped `pkexec` fallback for hidraw boot identities `2dc8:3208` and `2dc8:3002`; see [installation](install.md).
- An experimental Ultimate 2 receiver recovery helper that inspects state before permitting reset. A reset can install a valid pending image; see the [recovery contract](receiver-recovery.md).

`SourceReviewedAuthorization` and `LegacySourceReviewedAuthorization` describe experimental transactions. `FlashQualification` describes a demonstrated hardware update. The modern source-reviewed registry contains one exact 1.01 → 1.06 receiver authorization with full bootloader/application fingerprints. Hardware qualifications and legacy source-reviewed authorizations remain empty. UI choices and catalog results cannot populate these registries. See the [authorization requirements](protocol.md#authorization-and-hardware-qualification).

## Hardware evidence and limits

Read-only identification passed on both generations of receiver. Ultimate 2 paired status and the bounded radio identity/read path passed through receiver 1.06. Private reads obtained the receiver 1.01 application and both receiver bootloaders; the bootloaders are byte-identical. Private binaries and disassembly are not distributed.

The Ultimate 2 receiver 1.01 completed boot entry, PID and source inspection, one reset, and return to stable 1.01 on the same USB port with production helpers. Its application and erased metadata were unchanged. This used SteamOS’s existing access policy, with no permission changes or firmware initialization, erase, write, or commit. Pending-image copy, power-loss recovery, and actual firmware installation remain untested. The original Bluetooth receiver’s boot transition, live initialization tuple, and recovery remain untested.

A benign live check passed for worker launch through the systemd user manager with suspend inhibition. It did not transfer firmware or establish behavior during a Decky restart.

## Remaining work

1. Have Trevor prepare the Ultimate 2 receiver reporting 1.01 with official firmware 1.06, review the experimental evidence, and explicitly confirm the first update. Verify destination version and normal operation afterward; record failures without blindly retrying destructive commands.
2. Keep the unchanged-source recovery round trip distinct from pending-image activation and power-loss recovery, which remain untested. Record hardware qualification only after a demonstrated update and the required checks.
3. Review additional authorizations against exact source/destination bytes, model/role, transport, and recovery evidence. Controller, radio, and legacy native updates require separate work.
4. Validate SteamOS installation and upgrades, then broaden family support with corresponding protocol and hardware evidence. Preserve calibration and identity regions according to each protocol; do not infer a universal recovery or rollback mechanism.

Use unit tests for package parsing, authorization gates, transport framing, and failure handling, plus real hardware evidence for qualified model/role/revision combinations. Destructive power-loss testing requires a recoverable test target and a deliberate procedure.
