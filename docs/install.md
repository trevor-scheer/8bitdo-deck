# Install 8BitDo Updater

8BitDo Updater shares its backend between a desktop application, a standalone CLI, a Python API, and the 8BitDo Deck Decky plugin. Choose one or more frontends; their firmware compatibility checks, jobs, locks, and recovery rules are the same.

This is an early development build. Legacy installation through fwupd requires a supported 8BitDo device already in bootloader mode and a unique, trusted upgrade release. Linux enables three source-reviewed experimental routes after matching preflight: Ultimate 2 Wireless receiver stable 1.01 → official 1.06, original Ultimate Bluetooth receiver stable 1.03 → official 1.04, and Ultimate 2 Wireless controller stable 1.09 → official 1.10 over 2.4 GHz through receiver 1.06, edition 1. Each permits only its exact reviewed image. Normal direct-USB controller installation, Bluetooth installation, and other version combinations remain blocked. No native update is hardware-qualified; see the [protocol requirements](protocol.md#authorization-and-hardware-qualification).

## Choose a package

Download from [GitHub Releases](https://github.com/trevor-scheer/8bitdo-deck/releases). Version `0.3.0-alpha.3` uses `0.3.0a3` in asset filenames:

| Host | Desktop package | CLI package |
| --- | --- | --- |
| Linux x86-64, including Steam Deck | `8BitDo-Updater_0.3.0a3_linux-x86_64.AppImage` | `eightbitdo-updater-0.3.0a3-linux-x86_64.tar.gz` |
| Linux ARM64 | `8BitDo-Updater_0.3.0a3_linux-aarch64.AppImage` | `eightbitdo-updater-0.3.0a3-linux-aarch64.tar.gz` |
| macOS Apple Silicon | `8BitDo-Updater_0.3.0a3_macos-aarch64.dmg` | `eightbitdo-updater-0.3.0a3-macos-aarch64.tar.gz` |
| macOS Intel | `8BitDo-Updater_0.3.0a3_macos-x86_64.dmg` | `eightbitdo-updater-0.3.0a3-macos-x86_64.tar.gz` |
| Windows x86-64 | `8BitDo-Updater_0.3.0a3_windows-x86_64-setup.exe` or `-portable.zip` | `eightbitdo-updater-0.3.0a3-windows-x86_64.zip` |

Desktop and CLI packages bundle Python. They do not require Decky. Install version 0.3 manually once if you currently use 0.2; that release cannot check for app updates.

Linux retains its reviewed experimental firmware-installation paths. macOS and Windows support USB discovery, allowlisted identity/version queries, firmware browsing, and downloads. Native firmware writes, boot-mode transitions, and recovery are disabled there until physical-device correlation and reconnect behavior are validated. The interfaces show these limits; selecting another frontend does not bypass them.

## Verify the download

Download `SHA256SUMS` alongside the package. On Linux, check the files present in that directory:

```sh
sha256sum --check --ignore-missing SHA256SUMS
```

On macOS, run `shasum -a 256 ASSET_FILE`; in Windows PowerShell, run `Get-FileHash .\ASSET_FILE -Algorithm SHA256`. Replace `ASSET_FILE` with the downloaded filename and compare the complete digest with its entry in `SHA256SUMS`.

The [release procedure](releasing.md#verify-a-release-download) describes GitHub build-attestation verification. A checksum alone does not authenticate the publisher. Current macOS and Windows packages do not have Apple Developer ID/notarization or Windows Authenticode signatures, so the OS can show an unidentified-publisher warning or block launch. Signed TUF app updates authenticate update metadata and payloads; they do not remove those OS prompts.

## Install the desktop application

- **Linux/Steam Deck:** save the AppImage in a stable, user-owned location. In Desktop Mode, mark it executable in the file properties, or run `chmod +x ASSET_FILE.AppImage`, then launch it. Linux packages target glibc 2.35 or later; musl-based systems such as Alpine are outside the binary target. The [AppImage bundles the desktop dependencies](https://v2.tauri.app/distribute/appimage/). If the host lacks the FUSE runtime needed to mount an AppImage, install that runtime through the host's package manager before using the app.
- **macOS:** open the DMG and copy **8BitDo Updater.app** to Applications or another stable application directory you can write. The build targets macOS 11 or later. Use the package for your Mac's CPU architecture.
- **Windows:** run the `-setup.exe` installer for a per-user installation. Alternatively, extract the complete `-portable.zip` into a writable directory and run `eightbitdo-updater.exe`. Keep `eightbitdo.exe` and the license files alongside it. The desktop requires WebView2; the installer provisions that runtime when needed.

Open **My devices** and connect a controller or receiver by USB. **Scan devices** refreshes connected components; **Device library** browses models without identifying attached hardware. Controller and receiver firmware versions stay separate. Original SVG illustrations distinguish models, but their colors are illustrative and are not used for identification.

Review the selected component and release notes, then choose **Prepare firmware** to download and validate a release. Preparation does not write firmware. Eligible Linux jobs offer **Review installation**, followed by **Confirm firmware installation**. The backend rechecks the device and exact image before writing. The [firmware procedure](#prepare-and-install-firmware) covers evidence limits and recovery.

Use **App settings** for application updates, recovery-access setup, diagnostics, and the official web updater. The browser option supports only the models handled by that website.

## Install the standalone executable

Extract the matching CLI archive. For Linux x86-64:

```sh
tar -xzf eightbitdo-updater-0.3.0a3-linux-x86_64.tar.gz
cd eightbitdo-updater-0.3.0a3-linux-x86_64
./eightbitdo --version
./eightbitdo info
```

Use the equivalent macOS archive, or extract the Windows ZIP and run `.\eightbitdo.exe --version` and `.\eightbitdo.exe info` in PowerShell. These commands do not probe hardware.

You can place the executable in a user-owned directory on your `PATH`. Keep its path stable while jobs are running, and retain the notices and licenses when redistributing it. HTTPS catalog access and downloads use the host's trusted CA certificates.

Run `eightbitdo --licenses` to read the runtime notices embedded in that executable, including after an application self-update. This command does not access devices or create updater state.

## Linux firmware requirements

Run the updater as your normal desktop user. Device operations require access to the relevant HID nodes. Native installation and recovery also require a working systemd user manager, `systemd-run`, and `systemd-inhibit` so workers can prevent suspend. Those operations fail before writing if the protected launcher is unavailable. Bundled executables do not replace these host services.

`fwupd` is optional and supplies its own supported-device path and authorization policy. Receiver recovery access is described [below](#receiver-recovery-access).

## Update the application

In desktop **App settings**, choose a release channel, select **Check for app updates**, and review the available version. **Confirm app update** downloads and verifies the application package. When it is staged, use **Quit to finish updating**, then reopen the app. Closing finishes an application replacement; it does not confirm a controller firmware update.

Application replacement is blocked while firmware work is active. If the app reports an interrupted application update, its rollback action requires separate confirmation. See [application self-updates](self-updates.md) for supported installation layouts, CLI commands, waiting limits, and recovery.

Source checkouts, Python environments, and Decky use their original installation method for upgrades. Desktop app updates replace the frontend and its bundled backend together; they do not update a separately installed CLI or Decky plugin.

## Install the Python package

Download the wheel from [GitHub Releases](https://github.com/trevor-scheer/8bitdo-deck/releases). Create an isolated environment and install it:

```sh
python3 -m venv ~/.local/share/eightbitdo-venv
~/.local/share/eightbitdo-venv/bin/python -m pip install ./WHEEL_FILE
~/.local/share/eightbitdo-venv/bin/eightbitdo --version
```

On Windows, create a virtual environment with `python -m venv .venv`, then use `.\.venv\Scripts\python.exe -m pip install .\WHEEL_FILE` and `.\.venv\Scripts\eightbitdo.exe --version`.

Replace `WHEEL_FILE` with the downloaded `eightbitdo_updater-…-py3-none-any.whl` filename. Activate that environment or use its full executable path for later commands. The distribution name is `eightbitdo-updater`; the Python import remains `eightbitdo`. The `eightbitdo-deck` executable remains an alias for existing users.

For development, install the checkout with `python3 -m pip install -e .` inside a virtual environment. `python3 -m eightbitdo` uses that interpreter's installed package or the local checkout. The package includes its permission helper; a source checkout is not required for recovery setup.

## Get the plugin

For installation through Decky, download `8bitdo-deck.zip` from [GitHub Releases](https://github.com/trevor-scheer/8bitdo-deck/releases). Read the [Decky requirements](decky-requirements.md), then follow [Install through Decky](#install-through-decky). The release ZIP does not require Node.js, pnpm, Rust, or a separate Python-package installation.

Development packages are also available from successful [Checks workflow runs](https://github.com/trevor-scheer/8bitdo-deck/actions/workflows/checks.yml). GitHub wraps workflow artifacts in another ZIP: extract that download and use the inner `8bitdo-deck.zip` for installation.

### Build the plugin from source

Skip this section when installing a release ZIP. Use Python 3.11 or later, Node.js 22, and the pnpm version recorded in `package.json`. From the repository root, run:

```sh
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
pnpm run test:ui
python3 -m unittest discover -s tests -v
python3 scripts/package.py
```

The package is `out/8bitdo-deck.zip`. The packaging script uses an explicit file list and fixed ZIP metadata, then imports and initializes the packaged Python backend in an isolated temporary directory. It excludes firmware downloads, diagnostics, development dependencies, and frontend source maps.

## Install through Decky

Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader#installation) first if it is not already available. Decky 3.3.1 on SteamOS 3.9.5 is the tested combination, not a verified minimum version. The [Decky requirements and troubleshooting guide](decky-requirements.md) covers Python, systemd/session access, USB permissions, and optional tools. Normal setup on the tested SteamOS system needs no plugin-specific terminal commands.

1. Copy `8bitdo-deck.zip` to the Deck's Downloads folder.
2. In Gaming Mode, open Decky from the Quick Access menu, then open Decky's settings.
3. In **Settings → General**, enable **Developer mode** if the **Developer** page is hidden.
4. Open **Developer → Install Plugin from ZIP File → Browse** and select `8bitdo-deck.zip`. Confirm installation or replacement after checking the package source.
5. Open **8BitDo Deck** from the plugin list and choose **Scan connected devices**.

The [Decky developer settings implementation](https://github.com/SteamDeckHomebrew/decky-loader/blob/main/frontend/src/components/settings/pages/developer/index.tsx) supports installing a local ZIP. Labels can vary by Decky version and language.

The plugin runs as the desktop user and does not request Decky's root flag. It does not disable SteamOS's read-only system partition or install broad USB permission rules. Native fwupd operations use the system service and may require authorization under its policy. If a HID device cannot be opened, the plugin reports that limitation instead of changing system permissions.

## Receiver recovery access

On normal SteamOS, no terminal command or administrator setup is needed. The plugin recognizes the existing trusted SteamOS policy and checks actual device access. On the tested SteamOS 3.9.5 system, the root-owned `70-steam-jupiter-input.rules` already grants HID access to the active local user. Opening and closing the receiver's recovery interface as the desktop user succeeded without installing rules. The plugin still runs without Decky's root flag.

If another Linux system lacks suitable recovery access, select **Set up USB recovery** and approve its normal administrator prompt. If Gaming Mode cannot show the prompt, run the packaged fallback installer in Desktop Mode:

```sh
bash ~/homebrew/plugins/8bitdo-deck/scripts/install-udev-rules.sh
```

The fallback installer uses `pkexec` and adds rules only for USB vendor `2dc8`, recovery products `3208` and `3002`. It grants access to the active local user without changing firmware or running the plugin as root. The backend recognizes either the trusted SteamOS policy or the installed scoped rules and receipt; it also checks actual access when opening the device. A bounded wait accommodates delayed device access during re-enumeration. Permission checks do not replace device-specific recovery validation.

To remove the fallback rules, run the installer with `--uninstall`, then reconnect the receivers. Removing the Decky plugin alone does not remove those rules. This does not remove SteamOS's own policy.

The standalone CLI exposes the same access policy and setup:

```sh
eightbitdo recovery-access
eightbitdo setup-recovery-access --confirm
```

The first command only checks access configuration. The second requests administrator authorization for the scoped permission setup and waits up to five minutes for completion; it does not flash firmware. Keep the command running and approve the system prompt. It exits successfully only when access is configured. Denial, timeout, or Ctrl+C returns an error. A timeout or Ctrl+C stops waiting without cancelling the authorization process, so dismiss any remaining prompt before retrying setup. You can run `recovery-access` later to check the installed configuration.

## Prepare and install firmware

The desktop app and plugin automatically select the first identified component and load its matching firmware releases. If multiple components are connected, you can select another one from the device list. Controller and receiver firmware are separate. Manual model browsing remains available, but choosing a model manually does not prove that it matches attached hardware.

**Prepare firmware** does not write to a device. For vendor firmware it downloads and validates the image. For fwupd it prepares a trusted release plan; fwupd downloads and validates the firmware when installation begins.

The equivalent standalone workflow is:

```sh
eightbitdo scan
eightbitdo releases MODEL_ID
eightbitdo prepare MODEL_ID RELEASE_ID --device-id DEVICE_ID
eightbitdo job JOB_ID
```

Use identifiers returned by the preceding commands. Preparation runs in the background, so poll `job` until it leaves an active state. Review its target, version, evidence, and `can_install` result. To start an eligible update after that review, run `eightbitdo install JOB_ID --confirm`, then poll `job` through reconnect verification. Omitting `--device-id` prepares a download without an identified hardware target and cannot authorize installation.

The plugin offers **Review installation** only when the backend authorizes the prepared update. Native jobs require a matching developer-issued authorization and verified recovery access. Review the target, version, and evidence level, then select **Confirm firmware installation**. A source-reviewed experimental update has not been hardware-qualified. Keep the USB connection and power stable until the plugin reports verification complete.

Background jobs continue when the sidebar closes; reopening the plugin restores their status. Update workers run in transient user services outside Decky’s service. A benign live check verified worker launch and suspend inhibition without accessing hardware. Do not restart Decky or SteamOS during installation; surviving a Decky restart has not been validated. After a worker interruption, the app requires checking the device rather than retrying automatically.

The fwupd route preserves the system service's authorization and configured reporting policies. A device whose identity changes after leaving the bootloader may need a manual rescan; the app does not declare success solely because the install command exited successfully.

**Open official updater** prefers the installed Google Chrome Flatpak, then falls back to the desktop's `xdg-open` browser handler. The fallback browser may not support WebHID. Website support varies by model: the Ultimate Bluetooth receiver (`6008`) appears in its firmware catalog but has no update route in the inspected web implementation. This button is not a validated fallback for every detected device. Browser USB access and any vendor-specific connection instructions may need setup in Desktop Mode; the updater does not automatically change those permissions. A validated firmware download with a blocked installation state is not a completed device update.

### Experimental receiver updates

Choose the matching receiver and destination:

| Receiver | Required source | Authorized destination | Compatibility checks |
| --- | --- | --- | --- |
| Ultimate 2 Wireless (`6013`) | Stable 1.01 | Exact reviewed official 1.06 | Complete installed bootloader and application fingerprints, verified target, image layout, and checksum |
| Original Ultimate Bluetooth (`6008`) | Stable 1.03 | Exact reviewed official 1.04 | Verified model tag and boot target, reviewed packaged boot prefix, and exact initialization geometry |

The original receiver's installed bootloader bytes have not been fingerprinted. Its boot round trip and one existing application-page CRC passed. Both paths remain experimental: first installation, recovery reinstall, pending-image copy, and power-loss recovery have not been hardware-validated.

1. Keep the receiver connected to the Deck by USB, scan devices, and select its receiver entry.
2. Select its authorized destination above and choose **Prepare firmware**. Preparation validates the image and applicable source checks. A mismatch keeps installation blocked; legacy boot geometry is checked after confirmed entry and before erase.
3. Choose **Review installation** and read the experimental evidence, target, and version details.
4. To start the update, select **Confirm firmware installation**. Keep USB and power connected until the plugin verifies the returning receiver's version. Check normal controller/receiver operation afterward.

Preparation does not start the update. The user's final confirmation does. `QUALIFICATIONS` remains empty; these receiver records do not permit controller updates, other source versions, or different images. The separate controller authorization is described below.

### Experimental wireless controller update

This route is limited to the Ultimate 2 Wireless controller, stable **1.09 → exact official 1.10**, through an Ultimate 2 receiver running stable **1.06**, with controller edition **1**. Edition is not a reported hardware revision. Preparation fingerprints both components' bootloaders and installed firmware, checks controller battery and commit state, and binds the job to the selected receiver. An older receiver must complete its own eligible USB update first.

1. Connect the receiver to the Deck by USB, directly or through its USB-connected dock. Turn on its paired controller in **2.4 GHz mode** and charge it to at least **30%**. Keep a USB data cable available for recovery.
2. Scan and select **Ultimate 2 Wireless controller (via receiver)**. Select release **1.10**, then choose **Prepare firmware**. If the controller already runs 1.10, the app reports that no update is needed.
3. Review the source/destination versions and experimental evidence. Choose **Confirm firmware installation** only for an eligible prepared job.
4. Keep the controller powered on and the receiver connected until the app verifies the returning controller's version. Check normal controller operation afterward.

Other receivers can remain connected; jobs select and lock the original receiver. Each adapter carries one paired controller. Bluetooth and the controller's direct USB connection are not enabled for normal controller installation. The separate USB rescue operation requires its own confirmation before an erase/reinstall. Newly listed catalog firmware does not become installable until its source/image combination is reviewed.

The controller's bounded USB boot round trip passed on 1.10: boot identity and fingerprint matched, all 145408 application bytes and commit metadata remained unchanged, and the controller returned at 1.10. Complete wireless transfer, activation, USB rescue reinstall, and physical power-loss recovery remain untested. Static analysis identifies **LB+RB held during power-on/USB connection** as a forced recovery entry; that physical button procedure still needs hardware validation. See the [controller recovery contract](devices/ultimate2-controller-recovery.md) before attempting recovery. An interrupted operation must be inspected; do not automatically repeat its installation.

### Check an interrupted receiver update

For an eligible failed or blocked experimental receiver install, **Check interrupted update** inspects the receiver without changing mode, resetting, or writing firmware. Eligibility requires the same previously attempted job, its recorded original USB port and source version, the unchanged cached destination image, and its authorization still present in the compiled registry. A downloaded image or manually selected device cannot enable recovery.

The permitted action depends on the receiver:

- **Ultimate 2 Wireless:** **Confirm receiver recovery** reinspects and resets once if permitted. Reset can make the vendor bootloader finish an already committed image. This does not start another firmware transfer.
- **Original Ultimate Bluetooth:** **Confirm erase and reinstall** explicitly starts a complete erase and reinstall of the same cached, authorized destination image. The worker rechecks boot target and geometry before writing. This is destructive recovery, not a reset-only operation.

Both actions run under the update/device locks with suspend inhibition and repeat the job, image, authorization, port, and device checks. The original job completes only after the target version returns on the same port. An already installed target can be verified without writing; returning to the original version leaves the update incomplete. There is no automatic retry or boot-mode entry during recovery.

Keep USB and power connected. The two successful bounded boot round trips sent no erase, image write, or commit; they do not validate interrupted-write recovery or power-loss safety. See the [recovery contract and evidence limits](receiver-recovery.md).

From the CLI, use `eightbitdo check-recovery JOB_ID` for inspection. Read the reported action before running `eightbitdo recover JOB_ID --confirm`. The confirmation can authorize a reset or complete erase/reinstall, depending on the receiver and its checked state. Keep using the original job; creating another prepared download does not authorize recovery.

## State, diagnostics, and removal

Use **Export diagnostics** to save the discovered device information and warnings. The plugin displays the saved path. On Linux, runtime state normally lives in `~/.local/state/eightbitdo-deck/`, or under `$XDG_STATE_HOME/eightbitdo-deck/` if that variable is set. macOS and Windows use the per-user state directory selected by `platformdirs` for `8BitDo Updater`. Desktop and CLI share the default state on each host.

Use Decky's plugin settings to uninstall 8BitDo Deck. Decky removes plugin files; downloaded firmware, diagnostics, and job records can remain in the state directory. Remove those files only after all update jobs have finished.

The CLI uses the same state directory and can export diagnostics with `eightbitdo diagnostics`. Use the global `--state-dir PATH` option before the command only when you intentionally need a separate state directory. Separate directories do not share jobs or locks, so do not run them against the same device concurrently. Existing Decky jobs and cached firmware need no migration when using the default location.

To remove the standalone executable, remove its installed file after all jobs finish. To remove the Python package, uninstall `eightbitdo-updater` from its environment or remove that dedicated environment. Neither action removes firmware caches, job records, or installed udev rules. Do not replace or delete the executable or its environment while an update worker is running.

To remove the desktop application, wait for firmware and application-update jobs to finish. Delete the Linux AppImage or macOS app; use the Windows uninstaller for an installer-based installation, or remove the complete portable directory. Cached firmware, diagnostics, and job records remain in the user state directory.
