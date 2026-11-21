# Ultimate 2 receiver recovery

`eightbitdo.recovery` inspects the Ultimate 2 Wireless receiver (`6013`) in its USB bootloader and can request one reset after verifying its state. It does not initialize, erase, write, or commit firmware. **Resetting a verified pending update causes the vendor bootloader to copy staged firmware into the application area.** The helper returns a result that still requires reconnect verification.

This is source-reviewed experimental support. A bounded receiver 1.01 round trip passed with production helpers: boot identity and source inspection, one reset, and return to the unchanged application on the same USB port. Firmware installation, pending-image copy, and power-loss recovery remain untested. The plugin exposes a recovery check and separately confirmed action for eligible interrupted install jobs. One source-reviewed receiver 1.01 → exact official 1.06 authorization is enabled. Recovery eligibility still requires a previously attempted install with that authorization; hardware qualification and legacy authorization registries remain empty.

## Service and plugin flow

Recovery is limited to failed or blocked Ultimate 2 receiver install jobs that previously entered the source-reviewed experimental install path. A prepared download, arbitrary boot device, legacy receiver, or hardware qualification record is not eligible. The service requires the recorded original USB port and source version, the unchanged destination image in its validated cache, and a matching developer-issued `SourceReviewedAuthorization` still present in the compiled registry. UI arguments cannot supply that authorization.

- `check_recovery(job_id)`, shown as **Check interrupted update**, takes the update/device locks and reads the receiver on its recorded port. In application mode it checks the firmware version; in boot mode it calls `inspect_recovery`. It never changes mode or resets.
- After a check permits reset, **Confirm receiver recovery** calls `recover_update(job_id, confirmed=True)`. This starts a background worker with suspend inhibition. The worker repeats the job, image, authorization, port, and device-state checks before calling `finish_recovery`; an earlier successful check cannot bypass reinspection.
- After reset, the worker checks the same receiver's runtime identity and version. The target version completes the job. Returning to the original version leaves the update blocked as incomplete. A timeout stays blocked; reset is not retried.

Recovery requires verified access through the trusted SteamOS policy or the [scoped fallback setup](install.md#receiver-recovery-access). Normal SteamOS needs no administrator setup or terminal command; actual boot-node access is checked within a bounded wait. The service requires boot USB PID `3208`, the reviewed report layout, and verified firmware PID `6013`; a shared USB PID alone is insufficient. Recovery does not request a mode transition or begin a new firmware transfer.

## Protocol helper interface

The caller must hold the update and device locks, prevent suspend, and bind the boot HID node to the original physical USB port. Verify the reviewed unnumbered 64-byte report descriptor and obtain access before requesting any mode transition. A shared USB boot PID alone does not identify the firmware target.

Both entry points require a developer-issued `SourceReviewedAuthorization` and its exact destination image:

- `inspect_recovery(transport, image, authorization=...)` only reads. It returns `finish-pending`, `return-installed`, or `blocked`.
- `finish_recovery(transport, image, authorization=...)` repeats the full inspection and sends reset once only if the inspected state permits it. A transport error is not retried.

The caller must not construct authorizations from UI arguments or catalog responses. Source firmware versions in a record describe the reviewed policy; the helper does not infer a running version from boot mode.

## Reset gates

Every inspection validates the image and authorization before sending anything. It then queries firmware PID `6013` and hashes the entire bootloader against the reviewed fingerprint.

For a pending update, the helper requires all of these conditions:

1. The 52-byte metadata record at `0x12000` has a valid CRC16/MODBUS over its first 48 bytes, stored as a little-endian 32-bit value at byte 48.
2. Its pending marker, destination version, staging address `0x13000`, and image length match the authorized transaction.
3. Every staged image byte matches the authorized payload. Bytes through the end of its last 4096-byte sector must remain erased (`FF`), because the bootloader copies complete sectors.
4. The destination image satisfies the reviewed product, layout, extent, and additive-checksum constraints.

An erased factory record or a valid nonpending record permits reset only when the installed application matches either the reviewed source fingerprint or the exact authorized target image and passes the boot checksum checks. Otherwise inspection returns `blocked`. Malformed metadata, an unknown bootloader, a different target, or changed staging bytes cannot authorize reset.

## Bounded hardware validation

Receiver 1.01 completed this check using production helpers, a lock on its original USB port, and SteamOS’s existing access policy. No rules were installed and no administrator authorization or permission change was needed.

1. Read and fingerprint the complete `0x5000`-byte bootloader and `0x7500`-byte installed application. Production `C3` and complete 4096-byte readback verification also passed against existing staging bytes at `0x14000`, including the final two-byte read.
2. Send the vendor boot-entry report beginning `81 05 00 51 00`. Production `wait_for_boot` opened the same port’s `3208` interface and checked its report layout.
3. Run `inspect_recovery`: verify boot firmware PID `6013`, the full bootloader and source application, and the nonpending 52-byte metadata record containing only `FF` bytes.
4. Recheck that metadata remained entirely `FF`, then send reset `07` once. Production `wait_for_application` verified stable version 1.01 on the same port.
5. Recheck the source application SHA-256 and metadata after return. The 29,952 application bytes and all 52 metadata bytes were unchanged.

No `C1`, erase, write, or commit was sent. This demonstrates a return to an unchanged source image with no pending update; it does not validate the pending-copy path, interrupted-write recovery, or a complete firmware installation. A separate benign live check verified worker launch through the systemd user manager with suspend inhibition. An earlier `EACCES` had an unknown cause and did not establish a missing SteamOS access policy. Private dumps and disassembly are not distributed.

## Wire format and evidence

The reviewed receiver bootloader uses USB `2dc8:3208`, interrupt endpoints `84` and `05`, and unnumbered 64-byte HID reports. A Linux hidraw write contains a leading zero report selector followed by the 64-byte USB payload. That payload starts with class `05` and the 16-byte header. Input reports are 64 bytes beginning `00 05`; they do not gain another report selector. These rules follow the [Linux hidraw documentation](https://www.kernel.org/doc/html/latest/hid/hidraw.html).

The helper only sends commands `08` (product ID), `05` (absolute read, in chunks of at most 32 bytes), and `07` (reset). Parameters and header CRC are zero. It requires exact response status, command, length, CRC, total length, offset, and framing. No command is retried.

Static review of privately obtained, byte-identical receiver 1.01 and 1.06 bootloaders found:

- `0x1780`: bounded absolute-read handler; no flash mutation or initialization.
- `0x129c`: product-ID reply containing `6013`.
- `0x6d0`: metadata CRC validation; `0x7f4`: pending-image handling.
- `0x744`: installed-sector erase and copy, with the first sector copied last; staging is retained.
- `0x71c`: metadata persistence after copy. Metadata erase/write is not atomic or redundant.
- `0x18d4`: reset clears the recovery flag before restarting.

The reviewed [official receiver 1.06 image](https://support.8bitdo.com/bd-uploads/files/2025-09/7607115e6716732ae948b19514560cfe.dat) passes the same boot checksum constraints. See [protocol evidence](protocol.md) for the application transaction. Proprietary firmware dumps and disassembly are not distributed with this project. Simulated recovery tests verify host gating and command restrictions; they do not establish power-loss safety or hardware recovery success.
