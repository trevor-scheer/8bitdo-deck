# 8BitDo Deck

A Decky plugin and Linux command-line tool for 8BitDo firmware on Steam Deck.

**Early development build.** Device discovery, version queries, official firmware browsing, downloads, and package validation are implemented. Legacy installation delegates to fwupd for supported devices already in bootloader mode. Native Ultimate-series flashing is implemented at the protocol layer but remains disabled pending hardware qualification. No controller firmware has been flashed during this project's validation.

## Use it

Follow the [installation guide](docs/install.md) to build the plugin or download a ZIP from a successful [Checks run](https://github.com/trevor-scheer/8bitdo-deck/actions/workflows/checks.yml). Install the inner `8bitdo-deck.zip` through Decky's developer settings.

The plugin provides:

- USB discovery and model-specific connection guidance, with separate controller and receiver entries.
- Firmware version queries for the observed Ultimate Wireless and Ultimate 2 USB personalities. An ambiguous model stays unresolved.
- Official stable releases for Ultimate 2 controller/receiver, Ultimate Bluetooth controller/receiver, and the original Ultimate 2.4G controller.
- Bounded HTTPS downloads, exact size and firmware-header checks, local SHA-256 receipts, and explicit provenance limits.
- Background update jobs, targeted legacy fwupd installation, suspend inhibition during those installs, and post-update version checks.
- A launcher for the official WebHID updater in Chrome, plus sanitized diagnostic exports.

The dock is not assumed to be a separate firmware target. Its receiver has its own firmware.

## Support status

| Device or route | Implemented | Hardware validation |
| --- | --- | --- |
| Ultimate 2 Wireless receiver, USB `6013` | Identity, version, releases, download/header checks | Read-only checks passed on Steam Deck; receiver reported 1.06 |
| Ultimate 2 Wireless controller | Direct USB and paired status query code, releases, download/header checks | Direct controller connection and native updates still need testing |
| Original Ultimate Wireless, USB `3109` | Version query; manual model browsing | Reported 1.03; exact variant remains unresolved |
| Original Ultimate Bluetooth controller/receiver and 2.4G controller | Separate firmware catalog entries and download/header checks | Firmware files checked; native updates not qualified |
| Legacy fwupd `ebitdo` devices | Trusted upgrade selection and targeted installation for supported, bootloader-ready devices | Integration tested with simulated responses; no legacy device flashed here |
| Other 8BitDo products | Unknown devices remain visible; official updater link available | No native support claim |

**Prepare firmware does not flash a device.** An image that passes structural checks can still be blocked from installation. Manual model selection never authorizes a hardware write. The vendor's undocumented `md5` field does not match sampled complete files; the app records this without claiming that a locally calculated digest proves authenticity.

## Command line

The backend requires Python 3.11 or later and has no third-party Python dependencies. From the repository root:

```sh
python3 -m eightbitdo scan
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
python3 scripts/package.py
```

See the [implementation plan](docs/plan.md), [protocol and qualification requirements](docs/protocol.md), [catalog trust boundaries](docs/catalog.md), and [research](docs/research.md).

This is an independent project and is not affiliated with 8BitDo. Broad compatibility is the long-term goal; each additional device family requires its own evidence.
