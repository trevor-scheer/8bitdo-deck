# Install 8BitDo Deck

8BitDo Deck is an early development build. It can discover devices, browse official firmware, prepare updates, and open the official web updater. Legacy installation through fwupd requires a supported 8BitDo device already in bootloader mode and a unique, trusted upgrade release. Ultimate-series native writes remain disabled pending hardware qualification.

## Get the plugin

Use Python 3.11 or later, Node.js 22, and the pnpm version recorded in `package.json`. From the repository root, run:

```sh
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
python3 -m unittest discover -s tests -v
python3 scripts/package.py
```

The package is `out/8bitdo-deck.zip`. The packaging script uses an explicit file list and fixed ZIP metadata, then imports and initializes the packaged Python backend in an isolated temporary directory. It excludes firmware downloads, diagnostics, development dependencies, and frontend source maps.

Alternatively, download the **8bitdo-deck** artifact from a successful [Checks workflow run](https://github.com/trevor-scheer/8bitdo-deck/actions/workflows/checks.yml). GitHub wraps artifacts in another ZIP: extract that download and use the inner `8bitdo-deck.zip` for installation.

## Install through Decky

Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader#installation) first if it is not already available.

1. Copy `8bitdo-deck.zip` to the Deck's Downloads folder.
2. In Gaming Mode, open Decky from the Quick Access menu, then open Decky's settings.
3. Enable Decky's developer mode if the **Developer** page is hidden.
4. Open **Developer**, choose the ZIP installation option, and select `8bitdo-deck.zip`. Accept Decky's third-party plugin prompt after checking the package source.
5. Open **8BitDo Deck** from the plugin list and choose **Scan connected devices**.

The [Decky developer settings implementation](https://github.com/SteamDeckHomebrew/decky-loader/blob/main/frontend/src/components/settings/pages/developer/index.tsx) supports installing a local ZIP. Labels can vary by Decky version and language.

The plugin runs as the desktop user and does not request Decky's root flag. It does not disable SteamOS's read-only system partition or install broad USB permission rules. Native fwupd operations use the system service and may require authorization under its policy. If a HID device cannot be opened, the plugin reports that limitation instead of changing system permissions.

## Prepare and install firmware

Select a detected component or browse a model manually. Controller and receiver firmware are separate; choosing a model manually does not prove that it matches attached hardware.

**Prepare firmware** does not write to a device. For vendor firmware it downloads and validates the image. For fwupd it prepares a trusted release plan; fwupd downloads and validates the firmware when installation begins.

The plugin offers **Review installation** only when the backend has qualified the prepared update. Review the target and version, then select **Confirm firmware installation**. Keep the USB connection and power stable until the plugin reports verification complete. Background jobs continue when the sidebar closes; reopening the plugin restores their status. Do not restart Decky or SteamOS during installation: a service restart can terminate child workers. After a worker interruption, the app requires checking the device rather than retrying automatically.

The fwupd route preserves the system service's authorization and configured reporting policies. A device whose identity changes after leaving the bootloader may need a manual rescan; the app does not declare success solely because the install command exited successfully.

For Ultimate-series devices, use **Open official updater**. This launches the Google Chrome Flatpak installed through Discover. Chrome's USB access and any vendor-specific connection instructions may need setup in Desktop Mode; the plugin does not automatically change those permissions. A validated firmware download with a blocked installation state is not a completed device update.

## Diagnose or remove the plugin

Use **Export diagnostics** to save the discovered device information and warnings. The plugin displays the saved path. Runtime state normally lives in `~/.local/state/eightbitdo-deck/`, or under `$XDG_STATE_HOME/eightbitdo-deck/` if that variable is set.

Use Decky's plugin settings to uninstall 8BitDo Deck. Decky removes plugin files; downloaded firmware, diagnostics, and job records can remain in the state directory. Remove those files only after all update jobs have finished.
