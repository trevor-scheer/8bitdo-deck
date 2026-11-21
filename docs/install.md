# Install 8BitDo Deck

8BitDo Deck is an early development build. It can discover devices, browse official firmware, prepare updates, and open the official web updater. Legacy installation through fwupd requires a supported 8BitDo device already in bootloader mode and a unique, trusted upgrade release. One source-reviewed experimental native update is enabled after matching preflight: Ultimate 2 Wireless receiver stable 1.01 to the exact reviewed official 1.06 image. Native controller, wireless, and legacy updates remain blocked. No native update is hardware-qualified; see the [protocol requirements](protocol.md#authorization-and-hardware-qualification).

## Get the plugin

Use Python 3.11 or later, Node.js 22, and the pnpm version recorded in `package.json`. From the repository root, run:

```sh
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
pnpm run test:ui
python3 -m unittest discover -s tests -v
python3 scripts/package.py
```

The package is `out/8bitdo-deck.zip`. The packaging script uses an explicit file list and fixed ZIP metadata, then imports and initializes the packaged Python backend in an isolated temporary directory. It excludes firmware downloads, diagnostics, development dependencies, and frontend source maps.

Alternatively, download the **8bitdo-deck** artifact from a successful [Checks workflow run](https://github.com/trevor-scheer/8bitdo-deck/actions/workflows/checks.yml). GitHub wraps artifacts in another ZIP: extract that download and use the inner `8bitdo-deck.zip` for installation.

## Install through Decky

Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader#installation) first if it is not already available. Decky 3.3.1 is the tested loader version on SteamOS 3.9.5; older loaders can fail to initialize with newer Steam clients.

1. Copy `8bitdo-deck.zip` to the Deck's Downloads folder.
2. In Gaming Mode, open Decky from the Quick Access menu, then open Decky's settings.
3. Enable Decky's developer mode if the **Developer** page is hidden.
4. Open **Developer**, choose the ZIP installation option, and select `8bitdo-deck.zip`. Accept Decky's third-party plugin prompt after checking the package source.
5. Open **8BitDo Deck** from the plugin list and choose **Scan connected devices**.

The [Decky developer settings implementation](https://github.com/SteamDeckHomebrew/decky-loader/blob/main/frontend/src/components/settings/pages/developer/index.tsx) supports installing a local ZIP. Labels can vary by Decky version and language.

The plugin runs as the desktop user and does not request Decky's root flag. It does not disable SteamOS's read-only system partition or install broad USB permission rules. Native fwupd operations use the system service and may require authorization under its policy. If a HID device cannot be opened, the plugin reports that limitation instead of changing system permissions.

### Receiver recovery access

On normal SteamOS, no terminal command or administrator setup is needed. The plugin recognizes the existing trusted SteamOS policy and checks actual device access. On the tested SteamOS 3.9.5 system, the root-owned `70-steam-jupiter-input.rules` already grants HID access to the active local user. Opening and closing the receiver's recovery interface as the desktop user succeeded without installing rules. The plugin still runs without Decky's root flag.

If another Linux system lacks suitable recovery access, select **Set up receiver recovery** and approve its normal administrator prompt. If Gaming Mode cannot show the prompt, run the packaged fallback installer in Desktop Mode:

```sh
bash ~/homebrew/plugins/8bitdo-deck/scripts/install-udev-rules.sh
```

The fallback installer uses `pkexec` and adds rules only for USB vendor `2dc8`, recovery products `3208` and `3002`. It grants access to the active local user without changing firmware or running the plugin as root. The backend recognizes either the trusted SteamOS policy or the installed scoped rules and receipt; it also checks actual access when opening the device. A bounded wait accommodates delayed device access during re-enumeration. Permission checks do not replace device-specific recovery validation.

To remove the fallback rules, run the installer with `--uninstall`, then reconnect the receivers. Removing the Decky plugin alone does not remove those rules. This does not remove SteamOS's own policy.

## Prepare and install firmware

The plugin automatically selects the first identified component and loads its matching firmware releases. If multiple components are connected, you can select another one from the device list. Controller and receiver firmware are separate. Manual model browsing remains available, but choosing a model manually does not prove that it matches attached hardware.

**Prepare firmware** does not write to a device. For vendor firmware it downloads and validates the image. For fwupd it prepares a trusted release plan; fwupd downloads and validates the firmware when installation begins.

The plugin offers **Review installation** only when the backend authorizes the prepared update. Native jobs require a matching developer-issued authorization and verified recovery access. Review the target, version, and evidence level, then select **Confirm firmware installation**. A source-reviewed experimental update has not been hardware-qualified. Keep the USB connection and power stable until the plugin reports verification complete.

Background jobs continue when the sidebar closes; reopening the plugin restores their status. Update workers run in transient user services outside Decky’s service. A benign live check verified worker launch and suspend inhibition without accessing hardware. Do not restart Decky or SteamOS during installation; surviving a Decky restart has not been validated. After a worker interruption, the app requires checking the device rather than retrying automatically.

The fwupd route preserves the system service's authorization and configured reporting policies. A device whose identity changes after leaving the bootloader may need a manual rescan; the app does not declare success solely because the install command exited successfully.

**Open official updater** launches the vendor website in the Google Chrome Flatpak installed through Discover. Website support varies by model: the Ultimate Bluetooth receiver (`6008`) appears in its firmware catalog but has no update route in the inspected web implementation. This button is not a validated fallback for every detected device. Chrome's USB access and any vendor-specific connection instructions may need setup in Desktop Mode; the plugin does not automatically change those permissions. A validated firmware download with a blocked installation state is not a completed device update.

### First experimental receiver update

Only the Ultimate 2 Wireless receiver running stable 1.01 has a source-reviewed authorization. Its complete bootloader and installed application must match the compiled fingerprints, and the destination must be the exact reviewed official 1.06 image. An unreported board revision is constrained by those code fingerprints and verified firmware PID `6013`; the version label alone is insufficient.

1. Keep the receiver connected to the Deck by USB and scan devices. Select **Ultimate 2 Wireless receiver** reporting **1.01**, not its paired controller or the original Ultimate Bluetooth receiver.
2. Select official firmware **1.06** and choose **Prepare firmware**. Preparation verifies the image, target, source fingerprints, and recovery access. A mismatch keeps installation blocked.
3. Choose **Review installation** and read the source-reviewed experimental evidence and target/version details. The unchanged-firmware recovery round trip passed; first installation, pending-image copy, and power-loss recovery remain untested.
4. To perform the update, select **Confirm firmware installation**. Keep USB and power connected until the plugin verifies the returning receiver’s version. Check normal controller/receiver operation afterward.

Preparation does not start the update. The user’s final confirmation does. No hardware qualification or legacy source-reviewed record is enabled, and this authorization does not permit controller updates, radio firmware writes, other source versions, or a different image.

### Check an interrupted Ultimate 2 receiver update

For an eligible failed or blocked source-reviewed Ultimate 2 receiver install, **Check interrupted update** inspects the receiver without changing mode or resetting. Eligibility requires the previously attempted install, its original USB port and source version, its unchanged cached firmware image, and its authorization still present in the compiled registry. A downloaded image or manually selected device cannot enable recovery.

If inspection permits reset, the plugin offers **Confirm receiver recovery**. This explicit confirmation starts a background worker that repeats all checks, resets once if still required and permitted, and verifies the receiver on the same port. Reset may cause the vendor bootloader to install an already committed image. Returning to the original version leaves the update incomplete; a timeout does not trigger another reset. Keep USB and power connected.

The single 1.01 → 1.06 experimental authorization can support recovery only after an eligible install attempt; it does not enable recovery for an arbitrary attached device. A bounded hardware check on receiver 1.01 completed boot identity, inspection, one reset, and return to the unchanged source version on the same USB port. It used SteamOS’s existing access policy without administrator setup or permission changes. No firmware initialization, erase, write, or commit was sent. This does not validate pending-image copy or recovery from power loss; see the [recovery contract and evidence limits](receiver-recovery.md).

## Diagnose or remove the plugin

Use **Export diagnostics** to save the discovered device information and warnings. The plugin displays the saved path. Runtime state normally lives in `~/.local/state/eightbitdo-deck/`, or under `$XDG_STATE_HOME/eightbitdo-deck/` if that variable is set.

Use Decky's plugin settings to uninstall 8BitDo Deck. Decky removes plugin files; downloaded firmware, diagnostics, and job records can remain in the state directory. Remove those files only after all update jobs have finished.
