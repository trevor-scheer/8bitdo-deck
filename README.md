# 8BitDo Updater

A firmware companion for 8BitDo controllers and accessories, with a desktop app for Linux, macOS, and Windows, a standalone CLI, a Python API, and the **8BitDo Deck** frontend for Steam Deck. All frontends use the same backend and firmware policy.

**Early development build.** On Linux, three narrowly reviewed experimental updates are available: Ultimate 2 Wireless receiver **1.01 → 1.06**, original Ultimate Bluetooth receiver **1.03 → 1.04**, and Ultimate 2 Wireless controller **1.09 → 1.10 over 2.4 GHz**, through receiver **1.06**, for controller edition **1**. Each requires its exact reviewed image and matching preflight. No native update is hardware-qualified. Normal direct-USB controller installation, Bluetooth installation, and other version combinations remain blocked. No firmware image has been erased, written, or committed during this project's hardware validation.

## Use it

Download a package from [GitHub Releases](https://github.com/trevor-scheer/8bitdo-deck/releases):

- **Desktop application:** AppImage for Linux, DMG for macOS, or installer/portable ZIP for Windows. No Python or Decky installation is required.
- **Standalone CLI:** native executables for the same platforms, with Python bundled.
- **Python wheel or source distribution:** integrate the shared backend into another application, or install the CLI in a Python environment.
- **`8bitdo-deck.zip`:** use the same backend through Decky's Gaming Mode interface. See [Decky requirements and setup](docs/decky-requirements.md); the tested SteamOS setup needs no plugin-specific terminal commands or build tools.

In Decky, opening the plugin checks each identified device and automatically prepares its newest stable firmware. Choose **Install** on a ready device or **Update all** to install ready updates one at a time, without a separate review screen. Compact device rows show installed and available versions; **More options** contains manual downloads, USB setup, and diagnostics. Unsupported images remain blocked.

Follow the [installation guide](docs/install.md) for requirements, package verification, and setup. Linux and macOS packages cover x86-64 and ARM64; Windows packages cover x86-64. The repository and Decky plugin retain the `8bitdo-deck` name.

**macOS and Windows currently support discovery, reviewed identity/version queries, firmware browsing, and downloads. Firmware installation, boot transitions, and receiver recovery remain disabled on those hosts until their USB reconnect and recovery behavior is validated.** Native package builds do not establish firmware-update qualification.

Install 0.3 manually once if you use 0.2: the older release has no app-update mechanism. New desktop and standalone installations can check signed [application updates](docs/self-updates.md). Updating the application is separate from installing controller firmware. macOS and Windows packages currently lack Developer ID/notarization and Authenticode publisher signing; TUF signatures for app updates do not provide those OS identities.

The shared backend provides:

- USB discovery, automatic model selection for identified devices, and separate controller and receiver entries.
- Firmware version queries for the observed Ultimate Wireless and Ultimate 2 USB personalities, plus identification of paired Ultimate 2 controllers through their receivers. An ambiguous model stays unresolved.
- Experimental Ultimate 2 controller updates over 2.4 GHz for the exact reviewed route, with battery checks, complete source fingerprints, staging verification, and a separate USB rescue backend.
- Official stable releases for Ultimate 2 controller/receiver, Ultimate Bluetooth controller/receiver, and the original Ultimate 2.4G controller/receiver.
- Bounded HTTPS downloads, exact size and firmware-header checks, local SHA-256 receipts, and explicit provenance limits.
- Background jobs, native protocol backends, targeted legacy fwupd installation, suspend inhibition, and post-update version checks. Native jobs require a developer-issued authorization.
- Automatic recognition of SteamOS recovery USB access, without terminal commands or administrator setup. Other Linux systems can use a scoped installer through the normal administrator prompt.
- Sanitized diagnostic exports and a launcher for the official web updater. Website support and WebHID-capable browser availability vary by model and host.

The dock is not assumed to be a separate firmware target. Its receiver has its own firmware. The desktop and Decky interfaces share original, model-specific SVG illustrations; illustrated colors do not identify a connected device or hardware revision.

## Support status

The native firmware transactions and hardware observations below apply to Linux/SteamOS. A native macOS source test run completed 323 tests with 109 Linux-only skips; HTTPS catalog access and loading HIDAPI also passed. No 8BitDo device was attached to that Mac, so those checks do not validate device communication or firmware installation on macOS.

| Device or route | Implemented | Hardware validation |
| --- | --- | --- |
| Ultimate 2 Wireless receiver, USB `6013` | Discovery/catalog, experimental 1.01 → 1.06 update after matching preflight, recovery helper | Read-only checks passed on 1.06 and 1.01; receiver 1.01 completed boot identity, inspection, reset, and return to unchanged firmware on the same USB port; no firmware update tested |
| Ultimate 2 Wireless controller, 2.4 GHz via receiver | Experimental stable 1.09 → exact 1.10 through receiver 1.06, edition 1; separate confirmed USB rescue | Radio identity/reads passed; controller 1.10 completed USB boot inspection and return with full application and metadata unchanged; complete wireless transfer, rescue reinstall, and physical LB+RB recovery entry untested |
| Ultimate Bluetooth receiver, shared USB `3109` | Model-tag identification, catalog, experimental 1.03 → 1.04 update, confirmed full-image recovery reinstall | Boot identity, initialization geometry, one existing application-page CRC, and return to 1.03 passed; installed boot bytes, firmware update, and recovery reinstall unvalidated |
| Original Ultimate Bluetooth controller and 2.4G controller/receiver | Separate firmware catalog entries; legacy model-tag decoder; download/header checks | Firmware files checked; native updates not qualified |
| Legacy fwupd `ebitdo` devices | Trusted upgrade selection and targeted installation for supported, bootloader-ready devices | Integration tested with simulated responses; no legacy device flashed here |
| Other 8BitDo products | Unknown devices remain visible; official updater link available | No native support claim |

SteamOS 3.9.5 already grants recovery HID access. Both receiver boot round trips passed as the desktop user without installing rules or requesting administrator access. The Ultimate 2 receiver's complete source application and metadata were unchanged afterward; the original receiver check verified its returning version and one application-page CRC, without fingerprinting its installed bootloader. A controller running 1.10 also completed a USB boot round trip: its bootloader matched the reviewed fingerprint, all 145408 application bytes and metadata remained unchanged, and stable 1.10 returned. Firmware flashing, recovery reinstall, pending-image copy, and power-loss recovery remain untested; see the [receiver recovery findings](docs/receiver-recovery.md) and [controller USB recovery](docs/devices/ultimate2-controller-recovery.md).

The experimental receiver routes are **Ultimate 2 Wireless 1.01 → 1.06** and **Ultimate Bluetooth 1.03 → 1.04**. Decky prepares the newest stable image automatically and shows **Install** only for an eligible job; clicking it confirms installation. Experimental jobs are labeled in the device row. The desktop app retains its **Prepare firmware → Review installation → Confirm firmware installation** workflow. The modern path requires installed-code fingerprints; the original receiver instead requires the reviewed image, model, source version, and boot geometry. See the [installation procedure](docs/install.md#experimental-receiver-updates).

For the controller route, select **Ultimate 2 Wireless controller (via receiver)** while it is connected in 2.4 GHz mode. Charge it to at least 30% and keep a USB data cable available for recovery. An already-current 1.10 controller needs no reinstall. See the [wireless installation procedure](docs/install.md#experimental-wireless-controller-update) and [exact source/image scope](docs/devices/ultimate2-wireless.md). Future catalog releases require review before installation is enabled.

**Preparation does not flash a device**, whether started automatically in Decky or through **Prepare firmware** in the desktop app. An image that passes structural checks can still be blocked from installation. Manual model selection never authorizes a hardware write. The vendor's undocumented `md5` field does not match sampled complete files; the app records this without claiming that a locally calculated digest proves authenticity.

## Command line and API

With the standalone executable or Python package installed, run:

```sh
eightbitdo --help
eightbitdo --version
eightbitdo scan
eightbitdo models
eightbitdo releases ultimate2-receiver
eightbitdo prepare ultimate2-receiver RELEASE_ID
eightbitdo job JOB_ID
```

Replace `RELEASE_ID` with an ID returned by `releases` and `JOB_ID` with the ID returned by `prepare`. Add `--device-id DEVICE_ID` to preparation to bind it to an identified device from `scan`; preparation without a device browses/downloads only. Installation still requires an eligible job and explicit confirmation.

The Python package requires Python 3.11 or later; the standalone executable includes its interpreter. `python3 -m eightbitdo` and the installed `eightbitdo-deck` command remain available. On Linux, all frontends share the existing `~/.local/state/eightbitdo-deck/` state directory by default, including device locks and update jobs. macOS and Windows use their per-user application state locations.

Use the [public API](docs/api.md) to build another frontend. See the [architecture](docs/architecture.md) for the boundary between firmware logic, host services, and the desktop/CLI/Decky adapters. No network daemon is required.

## Development

To contribute another controller, receiver, accessory, or reviewed firmware version, use the repository's [device-support skill and contributor guide](https://github.com/trevor-scheer/8bitdo-deck/blob/main/CONTRIBUTING.md). The workflow covers discovery, protocol research, implementation, hardware validation, and upstream submission.

```sh
python3 -m unittest discover -s tests -v
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
pnpm run test:ui
pnpm run build:desktop
pnpm exec playwright install chromium
pnpm run test:desktop
python3 scripts/package.py
```

See the [release procedure](docs/releasing.md), [wireless update findings](docs/wireless-updates.md), [implementation plan](docs/plan.md), [protocol and authorization requirements](docs/protocol.md), [original receiver protocol](docs/legacy-receiver-protocol.md), [catalog trust boundaries](docs/catalog.md), and [research](docs/research.md).

This is an independent project and is not affiliated with 8BitDo. Broad compatibility is the long-term goal; each additional device family requires its own evidence.
