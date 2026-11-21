# Implementation plan

Status: Decky-first direction and initial two-pair scope approved on 2026-11-21. The first plugin, CLI, catalog, job engine, legacy fwupd integration, and experimental native protocol are implemented. Read-only discovery passed on the connected Deck receivers. First-generation Bluetooth receiver identification and Ultimate 2 wireless status/identity reads are validated. Native firmware writes, direct-controller testing, and wireless recovery qualification remain outstanding. An experimental flash requires an identified target, a validated image, and a reviewable recovery procedure.

## Product scope

Build a Decky plugin for Gaming Mode backed by a reusable Linux update engine and command-line interface. A standalone app can use the same engine later. Users should not need to know which official updater handles their model.

Start with the two owned controller/receiver pairs. Expand to other controllers, USB adapters, retro receivers, keyboards, and other accessories through explicit support entries. Display unknown and unvalidated devices honestly rather than treating brand detection as firmware compatibility.

First evaluate the official web updater on these models. Its public WebHID implementation may provide enough protocol evidence to avoid reverse engineering desktop binaries for some families. A browser route may solve the immediate need, but must be tested on SteamOS and does not establish complete brand coverage.

## Proposed components

- **Registry:** exact model, component role, hardware revision where available, normal and bootloader identities, protocol backend, firmware catalog key, connection instructions, and validation evidence.
- **Discovery:** Linux USB/HID enumeration, then documented identity and version queries. Manual selection narrows candidates but cannot override a hardware mismatch. USB descriptor versions are not assumed to be firmware versions.
- **Firmware catalog:** obtain releases from verified vendor sources, preserve source and digest, validate package structure and target compatibility, and cache complete downloads before flashing. A locally calculated hash detects later corruption; it does not authenticate an untrusted download. Firmware redistribution is outside the initial scope.
- **Update engine:** adapters for distinct protocol families; evaluate fwupd for supported legacy hardware and native HID transports for modern devices. Firmware writes must not depend on the frontend staying open.
- **Decky interface:** controller-friendly navigation, separate controller/receiver version rows, connection guidance, progress, and recovery instructions. Unsupported targets cannot offer an active update button.

Use the Decky template's React/TypeScript frontend and evaluate a Python core with a small, explicit transport interface. Confirm packaging and USB permissions before committing to a backend library. Avoid disabling SteamOS's read-only system partition or granting every process unrestricted HID access.

## Milestones

1. **Identify and inspect.** Implement read-only enumeration and a support registry. Distinguish the first-generation Ultimate variant, the Ultimate 2 controller, and both receivers. Read firmware versions through documented queries. Show official release information and export sanitized diagnostics. Test the web updater's non-flashing path on the Deck.
2. **Update the owned devices.** Establish protocol behavior from official code, licensed implementations, and captured transactions where necessary. Document connection transitions, model verification, erase/write acknowledgments, completion checks, and recovery. Validate one exact controller and receiver target at a time before enabling it in the plugin.
3. **Package and expand.** Test installation and upgrade on SteamOS, then prepare Decky distribution. Add legacy fwupd coverage where both the installed plugin and firmware source support it. Add other device families with hardware evidence; keep untested models disabled.

## Verification before enabling firmware writes

- Resolve controller versus receiver and exact model before an erase command. A shared bootloader PID or a matching product name is insufficient.
- Finish downloading and validate the image, expected size, target fields, and available authenticity evidence before entering the destructive stage.
- Use one update job per physical target and a device lock. Prevent suspend during the job and restore the previous state when it ends.
- Handle unplug, timeout, re-enumeration, malformed replies, and lost frontend connections explicitly. Do not blindly retry erase/write commands or offer cancellation during a non-interruptible stage.
- Preserve calibration or identity regions when required by that protocol. Provide only recovery and backup capabilities demonstrated for the particular device.
- Require protocol completion evidence and, where supported, readback verification; reconnect and check the reported firmware version. Sending the last packet alone is not success.

Use unit tests for package parsing, target mismatches, and the update state machine; recorded transport fixtures for protocol behavior; and real hardware for each enabled model/role/revision. Simulated tests cannot establish that a device can recover from an interrupted write.

## Decisions remaining

Identify the original Ultimate variant from documented device queries or its model label before selecting its firmware. Separately arrange the first experimental hardware flash after its exact image, target, and recovery procedure are reviewable.
