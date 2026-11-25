# Decky plugin requirements

On the tested SteamOS setup, installing the release ZIP through Decky is enough: no terminal commands, extra Python packages, or administrator setup are needed for this plugin. Install Decky itself first using its [official installation procedure](https://github.com/SteamDeckHomebrew/decky-loader#installation).

This page describes **8BitDo Deck**, the Decky frontend. The standalone desktop application and CLI have [separate requirements](install.md) and do not need Decky.

## Tested configuration

The plugin was installed and exercised with **Decky Loader 3.3.1 on SteamOS 3.9.5**, including plugin version **0.3.0-alpha.3**, on November 22, 2026. This is a tested combination, not a verified minimum version or a guarantee of compatibility with every Steam client update. Other Decky/SteamOS combinations have not been qualified by this project.

The plugin declares Decky's `api_version: 1` and uses `@decky/api` 1.1.3 and `@decky/ui` 4.11.0 in its frontend build. These are API/library versions, not Decky Loader version requirements. The release ZIP contains the built frontend; users do not install npm packages separately.

Use a Decky release compatible with your Steam client. If Decky itself does not open or other plugins also fail to load, resolve the loader/client compatibility issue before troubleshooting firmware access.

## Runtime requirements

The following requirements apply to the release ZIP:

| Requirement | Purpose and setup |
| --- | --- |
| A working Decky installation in Gaming Mode | Loads the plugin frontend and Python backend. No other Decky plugin is required. |
| Python 3.11 or later | The backend requires Python 3.11+. With Decky's frozen host, detached workers use the host's `python3` executable from `PATH`. The Decky ZIP contains backend code, not a bundled Python interpreter. This differs from the standalone packages. |
| An active local user session and systemd user manager | Native installation and recovery run as the desktop user through `systemd-run --user`, with `systemd-inhibit` preventing sleep. Gaming Mode is sufficient; Desktop Mode is not required for normal updates. Both commands and the user's session bus must be available. |
| Read/write access to the selected USB HID interfaces | Needed for identity queries and firmware operations, including the device's recovery interface after it changes mode. The tested SteamOS policy supplies this access; see [USB permissions](#usb-permissions). |
| Writable application state and firmware cache | Jobs, locks, and downloads default to `~/.local/state/eightbitdo-deck`, or `$XDG_STATE_HOME/eightbitdo-deck` when configured. Keep the plugin files available while workers run. |
| HTTPS access and trusted system CA certificates | Firmware browsing and preparation contact `support.8bitdo.com` and `download.8bitdo.com` on port 443. Installing the plugin from a downloaded local ZIP does not require a browser. |

The Linux Decky firmware paths use Python's standard library and the host services above. They do not require a `pip install`, the standalone CLI, or the desktop app. Do not install the standalone package's dependencies into Decky's Python environment as a setup step.

The plugin runs without Decky's root flag (`flags: []`). It does not require disabling SteamOS's read-only system partition, running Decky plugins as root, or manually exporting `XDG_RUNTIME_DIR` or `DBUS_SESSION_BUS_ADDRESS`. Plugin **0.3.0-alpha.2 and later** reconstruct and validate the current user's session-bus environment when Decky's host omits it. A genuinely unavailable user session still blocks protected worker launch.

## Install or update through Decky

Wait for any firmware installation or recovery to finish before replacing the plugin.

1. Download `8bitdo-deck.zip` from [GitHub Releases](https://github.com/trevor-scheer/8bitdo-deck/releases), verify it using the [download instructions](install.md#verify-the-download), and save it in the Deck's Downloads folder. Use this asset, not GitHub's **Source code** ZIP.
2. Open **Decky → Settings → General** and enable **Developer mode** if the Developer page is hidden.
3. Open **Developer → Install Plugin from ZIP File → Browse** and select the downloaded ZIP.
4. Confirm installation or replacement, then open **8BitDo Deck**. It scans and prepares firmware automatically; use **Check for updates** to refresh later.

Developer mode exposes the ZIP installer; it is not a firmware privilege or a requirement to enable Steam/SteamOS developer debugging. The [upstream installer UI](https://github.com/SteamDeckHomebrew/decky-loader/blob/main/frontend/src/components/settings/pages/developer/index.tsx) defines these controls; wording can vary with loader version or language.

Use the same ZIP installation procedure for plugin upgrades. Standalone TUF app updates do not update the Decky plugin. A normal ZIP installation does not require `sudo`, manual extraction into Decky's plugin directory, or a service restart.

## Update firmware

Opening the panel checks identified devices and automatically downloads and validates newer stable vendor firmware, one device at a time. Devices already on the newest listed version show **Up to date**. Unknown devices are not assigned a model automatically. For fwupd devices, preparation selects trusted metadata; fwupd downloads the image when installation starts.

Choose **Install** in a ready device's row, or **Update all** to install all ready jobs sequentially. The button click confirms those exact prepared jobs. Experimental jobs remain labeled **Experimental**, and all device, source-version, image, and recovery-access checks still apply. A newer unsupported release stays unavailable; the plugin does not silently substitute an older release.

The queue continues when the sidebar closes and restores its status when reopened. It stops at the first failure without retrying or recovering automatically. After a successful queue, the panel checks again. If updating a receiver makes its controller eligible, that controller needs a new **Install** or **Update all** click.

Device rows show a small picture, component name, installed and available versions, and status. **Details** explains unavailable updates. **More options** exposes firmware details for ready updates, manual firmware downloads, USB setup, the official updater, diagnostics, and scan warnings.

## USB permissions

The plugin recognizes the reviewed, root-owned SteamOS `70-steam-jupiter-input.rules` policy and checks actual HID access when opening a device. On the tested SteamOS configuration, no additional rule or administrator prompt was needed.

On a modified SteamOS installation or another Linux distribution, open **More options** and check the recovery-access status. If it offers setup, select **Set up USB recovery** and approve the system administrator prompt. That fallback requires `pkexec`, Bash, and a working desktop authentication agent. It installs narrowly scoped rules for recovery interfaces `2dc8:3208` and `2dc8:3002`. It does not grant access to every HID device or make the plugin run as root. It also does not fix missing access to a device's normal-mode HID interface.

If Gaming Mode cannot display the authorization prompt, use the [Desktop Mode fallback procedure](install.md#receiver-recovery-access). Administrator authorization is conditional on needing that permission setup; it is not a requirement for every Steam Deck owner.

## Optional tools and device requirements

- **fwupd:** only needed for the separate, supported legacy fwupd route. Its absence can produce a warning under **More options** without blocking the native Ultimate receiver/controller paths.
- **Chrome or another WebHID-capable browser:** only needed to use **Open official updater**. Native updates do not use a browser or WebHID.
- **Node.js, pnpm, and development dependencies:** only needed to [build the plugin from source](install.md#build-the-plugin-from-source). Rust/Tauri build tools are for the standalone desktop app, not release-ZIP installation.
- **A USB data connection:** receivers connect directly or through a USB-connected dock. The experimental Ultimate 2 wireless controller route uses 2.4 GHz through a receiver running 1.06, requires at least 30% controller battery, and permits only the reviewed 1.09 → 1.10 image. Bluetooth updating is not enabled. See the [firmware procedures](install.md#prepare-and-install-firmware) for exact device/version eligibility and recovery instructions.

Meeting Decky's requirements does not authorize every cataloged firmware image. **Download firmware only**, an already-current version, or an unsupported source/destination pair is a firmware-support result, not a request to change Decky permissions.

## Troubleshoot prerequisites

| Symptom | Next step |
| --- | --- |
| No Developer page or ZIP installer | Enable **Developer mode** in Decky's General settings. Check the loader's own documentation if its settings differ. |
| Plugin does not load | Check Decky/Steam client compatibility and that you installed the release `8bitdo-deck.zip`. On a custom system, verify the Python requirement; build tools are not a runtime fix. |
| Worker reports missing `python3` | Check `python3 --version` in Desktop Mode. The host interpreter must be available to Decky and be Python 3.11+. The standalone executable's bundled Python does not supply Decky's worker interpreter. |
| Missing `XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS`, or failure to connect to the user bus | Update the plugin to 0.3.0-alpha.2 or later. If it still reports an unavailable desktop session, ensure the local user session and systemd user manager are running. Shell exports in Konsole do not repair an already-running Decky backend. |
| Cannot prevent suspend / user-session tools unavailable | Verify that the host supplies `systemd-run` and `systemd-inhibit`. Native writes require this protection; do not bypass it. |
| USB access denied or recovery access unavailable | Reconnect the device and check recovery-access status. Follow the scoped permission setup only if needed; do not add Decky's root flag or broadly change `/dev/hidraw*` permissions. |
| Firmware download fails | Check network access, the system clock, and trusted CA certificates. Do not disable HTTPS certificate verification. |

After resolving a prerequisite failure, select **Check for updates** to scan and prepare again. **Install** confirms one eligible job; **Update all** confirms the ready jobs shown at that moment. Neither action opens a separate review screen. Keep USB/power stable and do not restart Decky or SteamOS during a firmware operation.
