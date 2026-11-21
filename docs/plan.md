# Implementation plan

Status on 2026-11-21: the Decky plugin, Python CLI, catalog, job engine, fwupd integration, and native transaction backends are implemented. **Native installation remains disabled:** all authorization registries are empty. No firmware image erase, write, or commit has been sent during validation.

## Product scope

Provide one Steam Deck interface that identifies controllers and receivers automatically, selects the matching official firmware, and explains connection requirements. Start with the owned Ultimate Bluetooth and Ultimate 2 Wireless controller/receiver pairs. Extend support to other controllers and accessories through explicit model and protocol evidence; unknown devices stay visible without an installation claim.

The Decky plugin uses a reusable Python engine and CLI. A standalone app can reuse that engine later. The official web updater remains an optional launcher, not a verified update route for every cataloged model.

## Implemented

- USB/HID discovery, verified identity and version queries, separate controller/receiver entries, and sanitized diagnostics. The original Bluetooth receiver is identified by its model tag; manual selection cannot override a mismatch.
- Official catalogs, bounded downloads, image structure and target checks, and local SHA-256 receipts. Local hashes bind bytes but do not prove vendor authenticity.
- Background jobs, per-target locks, suspend inhibition, persistent progress, targeted legacy fwupd installation, and reconnect/version checks.
- Ultimate 2 application transactions with metadata guards, source fingerprint checks for experimental authorization, per-block verification, and a second complete verification pass before commit.
- A separate original Bluetooth receiver transaction integrated into the service, with authorization checks before boot entry, descriptor and firmware identity checks, and handoff on the same physical USB port. See the [legacy protocol](legacy-receiver-protocol.md).
- Recovery USB access setup through normal `pkexec` authorization. Rules apply only to hidraw boot identities `2dc8:3208` and `2dc8:3002`; see [installation](install.md).
- An experimental Ultimate 2 receiver recovery helper that inspects state before permitting reset. A reset can install a valid pending image; see the [recovery contract](receiver-recovery.md).

`SourceReviewedAuthorization` and `LegacySourceReviewedAuthorization` describe experimental transactions. `FlashQualification` describes a demonstrated hardware update. All three registries remain empty; UI choices and catalog results cannot populate them. See the [authorization requirements](protocol.md#authorization-and-hardware-qualification).

## Hardware evidence and limits

Read-only identification passed on both generations of receiver. Ultimate 2 paired status and the bounded radio identity/read path passed through receiver 1.06. Private reads obtained the receiver 1.01 application and both receiver bootloaders; the bootloaders are byte-identical. Private binaries and disassembly are not distributed.

The Ultimate 2 receiver running 1.01 entered recovery as `2dc8:3208` with the expected descriptor, but `EACCES` prevented GET_PID and reset. **The recovery round trip has not passed.** Administrator authorization and a physical power cycle await user action. The original Bluetooth receiver's boot transition, live initialization tuple, and recovery remain untested. Simulated tests establish host behavior, not recovery from interrupted writes.

## Remaining work

1. Complete recovery access setup and physically reconnect the affected Ultimate 2 receiver. Verify boot identity, reset, and return to the expected runtime version before claiming a completed round trip.
2. Review each proposed experimental authorization against the exact source version, destination image, model/role, and recovery evidence. Keep source-reviewed records distinct from hardware qualifications and disclose unresolved limits.
3. Have Trevor run an explicitly authorized update on one exact target at a time. Verify the reported destination version and normal operation afterward; record failures without retrying destructive commands blindly. Wireless transfer requires separate validation.
4. Validate SteamOS installation and upgrades, then broaden family support with corresponding protocol and hardware evidence. Preserve calibration and identity regions according to each protocol; do not infer a universal recovery or rollback mechanism.

Use unit tests for package parsing, authorization gates, transport framing, and failure handling, plus real hardware evidence for qualified model/role/revision combinations. Destructive power-loss testing requires a recoverable test target and a deliberate procedure.
