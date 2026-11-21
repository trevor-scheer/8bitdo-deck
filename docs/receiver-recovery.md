# Ultimate 2 receiver recovery

`eightbitdo.recovery` inspects the Ultimate 2 Wireless receiver (`6013`) in its USB bootloader and can request one reset after verifying its state. It does not initialize, erase, write, or commit firmware. **Resetting a verified pending update causes the vendor bootloader to copy staged firmware into the application area.** The helper returns a result that still requires reconnect verification.

This is source-reviewed experimental support, not a hardware-validated recovery procedure. The receiver reached the expected USB boot personality. Although the initial check failed with `EACCES`, production `wait_for_boot` later opened it as the desktop user using SteamOS’s existing policy, verified the original USB port and report layout, and sent no USB reports. No project rules or receipt were installed. The earlier failure’s cause remains unknown. Boot GET_PID/reset, a completed recovery round trip, and interrupted-update recovery have not been demonstrated. The plugin exposes a recovery check and separately confirmed recovery action for eligible interrupted install jobs. All authorization registries remain empty, so this build enables no recovery jobs.

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
