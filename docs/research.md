# Feasibility and evidence

Inspected on 2026-11-21. Technical feasibility is supported by an official Linux-aware web updater and existing native Linux implementations. Neither source inspection nor USB discovery proves that this project can safely update a particular device.

## Official tools

8BitDo's [software directory](https://app.8bitdo.com/) distinguishes Ultimate Software V2, Xbox-focused Ultimate Software X, Ultimate Software V1, and the legacy Upgrade Tool. Their model and operating-system coverage differs. The [Upgrade Tool page](https://support.8bitdo.com/firmware-updater.html) advertises automatic detection and manual firmware selection. The [V2 page](https://app.8bitdo.com/Ultimate-Software-V2/) documents firmware updates and model-specific connection instructions.

The official [web updater](https://web.8bitdo.com/) is a significant additional option. Its served JavaScript uses `navigator.hid.requestDevice`, includes a Linux permission guide, and contains these separate catalog entries:

| Target | PID in vendor table | Catalog type | Model |
| --- | --- | --- | --- |
| Ultimate 2 Wireless controller | `0x6012` | `94` | `81HE` |
| Ultimate 2 Wireless receiver | `0x6013` | `95` | `81HE` |
| Original Ultimate Bluetooth controller | `0x6007` | `41` | `80NA` |
| Original Ultimate Bluetooth receiver | `0x6008` | `42` | `80NA` |
| Ultimate 2.4G controller entries | `0x3012`, `0x3013` | `43` | `81HA` |

These are catalog observations, not a complete runtime identity map. The HID filter table also includes `0x3109`; this does not establish which first-generation controller variant is paired. Presence in bundled code does not prove that a target is offered by the live web interface or successfully updateable on SteamOS.

The Linux guide proposes a vendor-wide, world-writable HID rule. This project should evaluate narrower access and the installed browser's sandbox rather than automatically applying that rule. No rule was installed during research.

Source assets inspected directly:

- [`index-BXAl9J5z.js`](https://web.8bitdo.com/assets/index-BXAl9J5z.js), SHA-256 `acda72a5a0722853c1913e1a9fa577c9efe1e35a2fc660095e980664c0b08af1`.
- [`LinuxGuide-BOybvDTj.js`](https://web.8bitdo.com/assets/LinuxGuide-BOybvDTj.js), SHA-256 `b9b3cf966842a39737262cbcc7d90bd4da20b009fcbce3865c8cdbb67b5d4de7`.

These assets may change or disappear. Public availability is not an open-source license; no vendor implementation or firmware is copied into this repository.

## Desktop identity decoder

Static analysis of the official desktop updaters resolves the shared legacy USB identities:

- [Windows archive](https://download.8bitdo.com/Tools/FirmwareUpdater/8BitDo_Firmware_Updater_Win.zip), SHA-256 `88fd6af7eb62a33546415db75a40907ef9120d14f10c6cd7a4337ea59994a26f`.
- [macOS archive](https://download.8bitdo.com/Tools/FirmwareUpdater/8BitDo_Firmware_Updater_macOS.zip), version 2.5.2/build 5, SHA-256 `4f197854e6eca261a77b0a0fc03db60d9f0b860fcb511dadb8484f5d9edea887`.

The Windows methods `DinputHid::getNewGamePadDetails`, `DinputHid::findHIDWithGamePad`, and `Const::.cctor` establish the query bytes and [model-tag decoder](protocol.md#read-only-operations). Native `_ReadHidAdvance@8` (RVA `0x5630`) copies the complete 64-byte input without stripping its report ID. `IDLEJLUltimateReceiver` distinguishes runtime PID `6007` from firmware PID `6008`. The two `IDLEJLUltimate24GReceiver` classes select catalog type 44 and firmware PID `3013`, including the START alias.

The Mac resource `Contents/Resources/S8BitdoDevice.plist` separately lists controller/receiver catalog types 41/42 (Bluetooth) and 43/44 (2.4G). Its `3109` row alone is insufficient to identify a receiver: the Windows decoder confirms that this USB ID is shared. The web bundle's `3013 → type 43` entry conflicts with the desktop receiver mapping. The live type-44 release 1.10 has header PID `3013`, address `3000`, and SHA-256 `5cc0edba1d3dbdff88fe3c9e77bd376891ad8f56fea87cd33f43d20518379086`, supporting the receiver-specific catalog entry.

Only protocol facts were used; proprietary source or binaries are not included in this project and the vendor programs were not executed.

## Existing Linux work

| Source | Useful evidence | Limit |
| --- | --- | --- |
| [fwupd ebitdo](https://github.com/fwupd/fwupd/tree/main/plugins/ebitdo) | Legacy update implementation, firmware parsing, bootloader handling; inspected source headers specify LGPL-2.1-or-later | Inspected quirks omit the observed `3109` and `6013`; cannot assume support for modern Ultimate devices |
| [8bitdo-ultimate-re](https://github.com/gomezluisj/8bitdo-ultimate-re) | MIT-licensed native HID bootloader work, backup and readback verification | Author reports validation on Ultimate Bluetooth `80NA`, firmware 1.11, YiChip YCM1310; not evidence for the 2.4G variant or Ultimate 2 |
| [8bitdo-Linux-Software firmware analysis](https://github.com/ascendedent/8bitdo-Linux-Software/blob/main/docs/firmware.md) | Ultimate 2 controller/receiver types 94/95 and updater command analysis | Tools are read-only; writes unvalidated; no license found during inspection, so do not copy implementation |
| [ultimatecontroller-rs](https://github.com/s8n/ultimatecontroller-rs) | Configuration and discovery research; MIT declared in Cargo.toml | Explicitly excludes firmware flashing; some firmware-family descriptions conflict with more specific research |
| [8bitdo-ultimate2-linux](https://github.com/MiguelSanzBr/8bitdo-ultimate2-linux/blob/main/docs/PROTOCOL.md) | Author-reported Ultimate 2 configuration/version-query behavior through `6013` | Configuration success does not validate flashing |
| [Retro Keyboard Flasher](https://github.com/crackheadakira/8BitDoRetroKeyboardFlasher) | Separate Rust/HID updater for a keyboard model; GPL-3.0 | Different protocol and narrow hardware validation; illustrates accessory-specific work |

Inspected revisions: fwupd `cad845481d02d223adb97795edfd7a825cd3ac84`; ultimatecontroller-rs `5cefb1c58179f10b442bb6158f75e04a01690164`; 8bitdo-Linux-Software `ca71e01ad7316c61d2162fc92a63e8cb6f7d01ff`. Verify applicable file licenses before reuse.

## Live Deck observations

Read-only inspection found SteamOS 3.9.5, fwupd 2.1.7, Chrome installed as a Flatpak, and an existing Decky plugin directory. `lsusb` reported:

| USB identity | Reported product | Interpretation |
| --- | --- | --- |
| `2dc8:3109` | 8BitDo Ultimate Wireless Controller | Model tag `0023` identifies the Ultimate Bluetooth receiver; version 1.03 |
| `2dc8:6013` | 8BitDo Ultimate 2 | Separate product-ID query identifies Ultimate 2 Wireless receivers; versions 1.06 and 1.01 observed |

`fwupdmgr get-devices --json` did not list either 8BitDo device in its present mode. This is an observation, not proof that every possible bootloader mode is unsupported. `fwupdtool get-plugins --json` could not load its cache without additional permissions; privileges were not elevated.

Read-only HID queries and official firmware downloads/header checks passed on the Deck. The identified Bluetooth receiver selected catalog type 42 automatically and its official 1.04 image passed header validation. No bootloader transitions, firmware writes, or controller configuration changes were performed. The browser updater has not been exercised with these devices. Dock firmware has not been established as a separate target.

## Consequence for the project

Use a shared interface over multiple verified protocol backends. Start with exact owned models and their receivers, retain a clear unsupported state, and expand only as device evidence supports it. Supporting all 8BitDo products is an ongoing compatibility effort, not a claim the initial project can make.
