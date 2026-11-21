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
| `2dc8:3109` | 8BitDo Ultimate Wireless Controller | First-generation Ultimate identity; exact paired model and component role need confirmation |
| `2dc8:6013` | 8BitDo Ultimate 2 | Matches the vendor's Ultimate 2 Wireless receiver table entry |

`fwupdmgr get-devices --json` did not list either 8BitDo device in its present mode. This is an observation, not proof that every possible bootloader mode is unsupported. `fwupdtool get-plugins --json` could not load its cache without additional permissions; privileges were not elevated.

No vendor HID commands, bootloader transitions, firmware writes, installation changes, or controller configuration changes were performed. Firmware versions remain unknown. The browser updater has not been exercised with these devices. Dock firmware has not been established as a separate target.

## Consequence for the project

Use a shared interface over multiple verified protocol backends. Investigate the web implementation before spending time on desktop binary analysis. Start with exact owned models and their receivers, retain a clear unsupported state, and expand only as device evidence supports it. Supporting all 8BitDo products is an ongoing compatibility effort, not a claim the initial project can make.
