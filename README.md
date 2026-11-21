# 8BitDo Deck

A Decky plugin and Linux command-line tool for 8BitDo firmware on Steam Deck.

**Early development build.** Discovery, firmware browsing, package validation, and update jobs are implemented. Native Ultimate 2 and original Ultimate Bluetooth receiver transactions are integrated, but **native installation remains disabled: all authorization registries are empty**. Source-reviewed experimental authorization is separate from hardware qualification. No firmware image has been erased, written, or committed during this project's validation.

## Use it

Follow the [installation guide](docs/install.md) to build the plugin or download a ZIP from a successful [Checks run](https://github.com/trevor-scheer/8bitdo-deck/actions/workflows/checks.yml). Install the inner `8bitdo-deck.zip` through Decky's developer settings.

The plugin provides:

- USB discovery, automatic model selection for identified devices, and separate controller and receiver entries.
- Firmware version queries for the observed Ultimate Wireless and Ultimate 2 USB personalities. An ambiguous model stays unresolved.
- Official stable releases for Ultimate 2 controller/receiver, Ultimate Bluetooth controller/receiver, and the original Ultimate 2.4G controller/receiver.
- Bounded HTTPS downloads, exact size and firmware-header checks, local SHA-256 receipts, and explicit provenance limits.
- Background jobs, native protocol backends, targeted legacy fwupd installation, suspend inhibition, and post-update version checks. Native jobs require a developer-issued authorization.
- Automatic recognition of SteamOS recovery USB access, without terminal commands or administrator setup. Other Linux systems can use a scoped installer through the normal administrator prompt.
- A launcher for the official WebHID updater in Chrome, plus sanitized diagnostic exports.

The dock is not assumed to be a separate firmware target. Its receiver has its own firmware.

## Support status

| Device or route | Implemented | Hardware validation |
| --- | --- | --- |
| Ultimate 2 Wireless receiver, USB `6013` | Discovery/catalog, guarded native transaction, experimental recovery helper | Read-only checks passed on 1.06 and 1.01; recovery entry reached `3208`; nonprivileged access now passes, but boot GET_PID/reset and the complete round trip remain unverified |
| Ultimate 2 Wireless controller | Paired status, bounded radio identity probe, catalog, native application transaction | Two controllers reported 1.10; radio identity and firmware reads passed through receiver 1.06; no native update or controller recovery tested |
| Ultimate Bluetooth receiver, shared USB `3109` | Model-tag identification, catalog, separate boot transaction and service integration | Identified at 1.03 and validated official 1.04 download; boot transition and complete update remain untested |
| Original Ultimate Bluetooth controller and 2.4G controller/receiver | Separate firmware catalog entries; legacy model-tag decoder; download/header checks | Firmware files checked; native updates not qualified |
| Legacy fwupd `ebitdo` devices | Trusted upgrade selection and targeted installation for supported, bootloader-ready devices | Integration tested with simulated responses; no legacy device flashed here |
| Other 8BitDo products | Unknown devices remain visible; official updater link available | No native support claim |

SteamOS 3.9.5 already grants recovery HID access; opening the receiver as the desktop user succeeded without installing rules. The earlier `EACCES` does not establish a missing OS policy, and its cause remains unknown. Boot GET_PID/reset and the recovery round trip have not completed. See the [recovery findings](docs/receiver-recovery.md).

**Prepare firmware does not flash a device.** An image that passes structural checks can still be blocked from installation. Manual model selection never authorizes a hardware write. The vendor's undocumented `md5` field does not match sampled complete files; the app records this without claiming that a locally calculated digest proves authenticity.

## Command line

The backend requires Python 3.11 or later and has no third-party Python dependencies. From the repository root:

```sh
python3 -m eightbitdo scan
python3 -m eightbitdo probe-wireless
python3 -m eightbitdo models
python3 -m eightbitdo releases ultimate2-receiver
python3 -m eightbitdo prepare ultimate2-receiver RELEASE_ID
python3 -m eightbitdo job JOB_ID
```

Replace `RELEASE_ID` with an ID returned by `releases` and `JOB_ID` with the ID returned by `prepare`. Use `--help` for diagnostics and the qualified fwupd installation path. State is stored under `~/.local/state/eightbitdo-deck/` by default.

## Development

```sh
python3 -m unittest discover -s tests -v
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
pnpm run test:ui
python3 scripts/package.py
```

See the [wireless update findings](docs/wireless-updates.md), [implementation plan](docs/plan.md), [protocol and authorization requirements](docs/protocol.md), [original receiver protocol](docs/legacy-receiver-protocol.md), [catalog trust boundaries](docs/catalog.md), and [research](docs/research.md).

This is an independent project and is not affiliated with 8BitDo. Broad compatibility is the long-term goal; each additional device family requires its own evidence.
