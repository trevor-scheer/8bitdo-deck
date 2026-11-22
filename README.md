# 8BitDo Updater

A shared Linux firmware updater for 8BitDo controllers and accessories, with a standalone command-line application, a Python API, and the **8BitDo Deck** Decky frontend.

**Early development build.** Discovery, firmware browsing, package validation, and update jobs are implemented. **Two experimental native receiver updates are available:** Ultimate 2 Wireless 1.01 → official 1.06 and original Ultimate Bluetooth 1.03 → official 1.04. Each requires its exact reviewed image and matching preflight. No native update is hardware-qualified. Controller updates, wireless firmware writes, and other native version combinations remain blocked. No firmware image has been erased, written, or committed during this project's validation.

## Use it

Download a package from [GitHub Releases](https://github.com/trevor-scheer/8bitdo-deck/releases):

- **Standalone Linux executable:** use the command line without installing Python or Decky. Choose the binary for your CPU architecture.
- **Python wheel or source distribution:** integrate the shared backend into another application, or install the CLI in a Python environment.
- **`8bitdo-deck.zip`:** use the same backend through Decky's Gaming Mode interface.

Follow the [installation guide](docs/install.md) for requirements, package verification, and setup. The executable targets Linux; macOS and Windows device backends are not implemented. The repository and Decky plugin retain the `8bitdo-deck` name.

The shared backend provides:

- USB discovery, automatic model selection for identified devices, and separate controller and receiver entries.
- Firmware version queries for the observed Ultimate Wireless and Ultimate 2 USB personalities. An ambiguous model stays unresolved.
- Official stable releases for Ultimate 2 controller/receiver, Ultimate Bluetooth controller/receiver, and the original Ultimate 2.4G controller/receiver.
- Bounded HTTPS downloads, exact size and firmware-header checks, local SHA-256 receipts, and explicit provenance limits.
- Background jobs, native protocol backends, targeted legacy fwupd installation, suspend inhibition, and post-update version checks. Native jobs require a developer-issued authorization.
- Automatic recognition of SteamOS recovery USB access, without terminal commands or administrator setup. Other Linux systems can use a scoped installer through the normal administrator prompt.
- Sanitized diagnostic exports and a launcher for the official web updater. Website support and WebHID-capable browser availability vary by model and host.

The dock is not assumed to be a separate firmware target. Its receiver has its own firmware.

## Support status

| Device or route | Implemented | Hardware validation |
| --- | --- | --- |
| Ultimate 2 Wireless receiver, USB `6013` | Discovery/catalog, experimental 1.01 → 1.06 update after matching preflight, recovery helper | Read-only checks passed on 1.06 and 1.01; receiver 1.01 completed boot identity, inspection, reset, and return to unchanged firmware on the same USB port; no firmware update tested |
| Ultimate 2 Wireless controller | Paired status, bounded radio identity probe, catalog; native writes blocked | Two controllers reported 1.10; radio identity and firmware reads passed through receiver 1.06; no native update or controller recovery tested |
| Ultimate Bluetooth receiver, shared USB `3109` | Model-tag identification, catalog, experimental 1.03 → 1.04 update, confirmed full-image recovery reinstall | Boot identity, initialization geometry, one existing application-page CRC, and return to 1.03 passed; installed boot bytes, firmware update, and recovery reinstall unvalidated |
| Original Ultimate Bluetooth controller and 2.4G controller/receiver | Separate firmware catalog entries; legacy model-tag decoder; download/header checks | Firmware files checked; native updates not qualified |
| Legacy fwupd `ebitdo` devices | Trusted upgrade selection and targeted installation for supported, bootloader-ready devices | Integration tested with simulated responses; no legacy device flashed here |
| Other 8BitDo products | Unknown devices remain visible; official updater link available | No native support claim |

SteamOS 3.9.5 already grants recovery HID access. Both receiver boot round trips passed as the desktop user without installing rules or requesting administrator access. The Ultimate 2 receiver's complete source application and metadata were unchanged afterward; the original receiver check verified its returning version and one application-page CRC, without fingerprinting its installed bootloader. Firmware flashing, recovery reinstall, pending-image copy, and power-loss recovery remain untested; see the [recovery findings](docs/receiver-recovery.md).

For an experimental update, select the receiver and its authorized destination: **Ultimate 2 Wireless 1.01 → 1.06** or **Ultimate Bluetooth 1.03 → 1.04**. Prepare it, review the evidence and limits, then select **Confirm firmware installation** to start. The modern path requires installed-code fingerprints; the original receiver instead requires the reviewed image, model, source version, and boot geometry. See the [installation procedure](docs/install.md#experimental-receiver-updates).

**Prepare firmware does not flash a device.** An image that passes structural checks can still be blocked from installation. Manual model selection never authorizes a hardware write. The vendor's undocumented `md5` field does not match sampled complete files; the app records this without claiming that a locally calculated digest proves authenticity.

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

The Python package requires Python 3.11 or later; the standalone executable includes its interpreter. `python3 -m eightbitdo` and the installed `eightbitdo-deck` command remain available. All frontends share the existing `~/.local/state/eightbitdo-deck/` state directory by default, including device locks and update jobs.

Use the [public API](docs/api.md) to build another frontend. See the [architecture](docs/architecture.md) for the boundary between firmware logic, Linux services, and the CLI/Decky adapters. No network daemon is required.

## Development

To contribute another controller, receiver, accessory, or reviewed firmware version, use the repository's [device-support skill and contributor guide](https://github.com/trevor-scheer/8bitdo-deck/blob/main/CONTRIBUTING.md). The workflow covers discovery, protocol research, implementation, hardware validation, and upstream submission.

```sh
python3 -m unittest discover -s tests -v
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
pnpm run test:ui
python3 scripts/package.py
```

See the [release procedure](docs/releasing.md), [wireless update findings](docs/wireless-updates.md), [implementation plan](docs/plan.md), [protocol and authorization requirements](docs/protocol.md), [original receiver protocol](docs/legacy-receiver-protocol.md), [catalog trust boundaries](docs/catalog.md), and [research](docs/research.md).

This is an independent project and is not affiliated with 8BitDo. Broad compatibility is the long-term goal; each additional device family requires its own evidence.
