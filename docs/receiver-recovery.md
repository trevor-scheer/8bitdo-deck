# Receiver recovery

Recovery is available only for an eligible interrupted experimental install job. The two authorized transactions are Ultimate 2 Wireless receiver (`6013`) 1.01 → exact official 1.06 and original Ultimate Bluetooth receiver (`6008`) 1.03 → exact official 1.04. Neither has a hardware qualification. First firmware installation, recovery reinstall, pending-image copy, and power-loss recovery remain untested.

The recovery action differs by receiver. The modern helper can reset after inspecting installed or staged firmware; **reset can make its bootloader copy an already committed image into the application area**. The original receiver requires **a complete erase and reinstall of the cached authorized image**, with separate explicit confirmation. Neither path retries automatically.

## Service and plugin flow

Recovery is limited to failed or blocked receiver jobs that previously entered the source-reviewed experimental install path. A prepared download, arbitrary boot device, or hardware qualification record is not eligible. The service requires the captured original device context, USB port and source version, the unchanged destination image in its validated cache, and the matching developer-issued authorization still present in the compiled registry. UI arguments cannot create an authorization or replace the original context.

- `check_recovery(job_id)`, shown as **Check interrupted update**, takes the update/device locks and inspects only the recorded port. In application mode it checks identity and version; in boot mode it uses the appropriate receiver helper. It never changes mode, resets, or writes firmware.
- When inspection permits an action, **Confirm receiver recovery** authorizes the modern reset path; **Confirm erase and reinstall** authorizes the legacy full-image reflash. Both call `recover_update(job_id, confirmed=True)` for the same original job after a successful check.
- The background worker prevents suspend and repeats the job, image, authorization, port, and device-state checks. An earlier check cannot bypass reinspection. It performs the permitted operation and verifies the target runtime identity/version on the same port before completing the job.
- An already installed target can complete through verification without writing. Returning to the original version leaves the update blocked as incomplete. A timeout does not trigger another reset or transfer.

Recovery requires verified access through the trusted SteamOS policy or the [scoped fallback setup](install.md#receiver-recovery-access). Normal SteamOS needs no administrator setup or terminal command. The service opens boot USB PID `3208` on the original port, checks the reviewed report layout and actual access, and queries the model-specific firmware PID. A shared USB PID alone is insufficient. Recovery does not request a mode transition.

## Original Ultimate Bluetooth receiver

The service validates the cached image and `LegacySourceReviewedAuthorization` before calling `inspect_receiver_boot`. That helper queries firmware PID `6008` and requires the exact initialization tuple `(4000, ffffffff, 0, 1000)`. It sends only target query `08` and default `C1`; the reviewed `C1` path reads filesystem metadata and updates a RAM handle. It does not erase, write, commit, reset, or enter boot mode.

A successful inspection offers action `reflash`. After **Confirm erase and reinstall**, the worker calls `flash_boot_receiver`, which rechecks target and geometry before erasing. It reinstalls the complete authorized application image after its preserved `0x4000`-byte boot prefix, verifies every page, finalizes with `C2`, and resets. The same original job is reused. Only a verified return to the destination version completes it. The original runtime source version is taken from the recorded attempted job; it is not inferred from a bootloader reply.

The 1.03 receiver completed a bounded production round trip: boot `3208`, firmware PID `6008`, exact `C1` geometry, existing application-page `C3` CRC `75a9`, one reset, and same-port runtime 1.03. The reset disconnected during input polling before delivering an acknowledgment; the runtime check established completion. No erase, image write, or `C2` was sent. This verifies one application-page CRC and the boot round trip, **not the installed bootloader bytes or recovery after interrupted writes**. The packaged boot prefix is checked against the authorization, but installed boot bytes have not been fingerprinted. See the [legacy protocol evidence](legacy-receiver-protocol.md).

## Ultimate 2 Wireless receiver helper interface

The caller must hold the update and device locks, prevent suspend, and bind the boot HID node to the original physical USB port. Verify the reviewed unnumbered 64-byte report descriptor and obtain access before requesting any mode transition. A shared USB boot PID alone does not identify the firmware target.

Both entry points require a developer-issued `SourceReviewedAuthorization` and its exact destination image:

- `inspect_recovery(transport, image, authorization=...)` only reads. It returns `finish-pending`, `return-installed`, or `blocked`.
- `finish_recovery(transport, image, authorization=...)` repeats the full inspection and sends reset once only if the inspected state permits it. A transport error is not retried.

The caller must not construct authorizations from UI arguments or catalog responses. Source firmware versions in a record describe the reviewed policy; the helper does not infer a running version from boot mode.

### Reset gates

Every inspection validates the image and authorization before sending anything. It then queries firmware PID `6013` and hashes the entire bootloader against the reviewed fingerprint.

For a pending update, the helper requires all of these conditions:

1. The 52-byte metadata record at `0x12000` has a valid CRC16/MODBUS over its first 48 bytes, stored as a little-endian 32-bit value at byte 48.
2. Its pending marker, destination version, staging address `0x13000`, and image length match the authorized transaction.
3. Every staged image byte matches the authorized payload. Bytes through the end of its last 4096-byte sector must remain erased (`FF`), because the bootloader copies complete sectors.
4. The destination image satisfies the reviewed product, layout, extent, and additive-checksum constraints.

An erased factory record or a valid nonpending record permits reset only when the installed application matches either the reviewed source fingerprint or the exact authorized target image and passes the boot checksum checks. Otherwise inspection returns `blocked`. Malformed metadata, an unknown bootloader, a different target, or changed staging bytes cannot authorize reset.

### Bounded hardware validation

Receiver 1.01 completed this check using production helpers, a lock on its original USB port, and SteamOS’s existing access policy. No rules were installed and no administrator authorization or permission change was needed.

1. Read and fingerprint the complete `0x5000`-byte bootloader and `0x7500`-byte installed application. Production `C3` and complete 4096-byte readback verification also passed against existing staging bytes at `0x14000`, including the final two-byte read.
2. Send the vendor boot-entry report beginning `81 05 00 51 00`. Production `wait_for_boot` opened the same port’s `3208` interface and checked its report layout.
3. Run `inspect_recovery`: verify boot firmware PID `6013`, the full bootloader and source application, and the nonpending 52-byte metadata record containing only `FF` bytes.
4. Recheck that metadata remained entirely `FF`, then send reset `07` once. Production `wait_for_application` verified stable version 1.01 on the same port.
5. Recheck the source application SHA-256 and metadata after return. The 29,952 application bytes and all 52 metadata bytes were unchanged.

No `C1`, erase, write, or commit was sent. This demonstrates a return to an unchanged source image with no pending update; it does not validate the pending-copy path, interrupted-write recovery, or a complete firmware installation. A separate benign live check verified worker launch through the systemd user manager with suspend inhibition. An earlier `EACCES` had an unknown cause and did not establish a missing SteamOS access policy. Private dumps and disassembly are not distributed.

### Wire format and evidence

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
