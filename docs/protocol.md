# Native protocol implementation

The native backend contains an application-mode update transaction for Ultimate 2 Wireless controllers (`6012`) and receivers (`6013`). It is **not hardware-qualified**. The support registry must keep native flashing disabled until the qualification below is complete. Unit-test devices simulate the protocol and do not establish hardware compatibility.

## Evidence

The implementation was independently written from the observable wire format and these sources, inspected on 2026-11-21:

- The official [web updater's connection and update module](https://web.8bitdo.com/assets/connectDevice-CqNxlsyD.js) defines the 28-byte image header, application command framing, CRC algorithm, update sequence, and separate controller/receiver handling.
- The official [main module](https://web.8bitdo.com/assets/index-BXAl9J5z.js) defines product/catalog identities and command constants.
- [Firmware analysis](https://github.com/ascendedent/8bitdo-Linux-Software/blob/main/docs/firmware.md) and [captured identify traffic](https://github.com/ascendedent/8bitdo-Linux-Software/blob/main/docs/capture-log.md) corroborate the identify command, report layout, and distinctions between USB personalities. This repository has no identified license; its implementation code was not copied.
- [Ultimate 2 configuration protocol](https://github.com/MiguelSanzBr/8bitdo-ultimate2-linux/blob/main/docs/PROTOCOL.md) reports hardware verification of version and paired-controller queries and distinguishes the edition field from a firmware product ID.

The published vendor code is evidence, not an imported dependency. It does not establish that every protocol path works on the connected hardware.

## Read-only operations

`query_identity` sends a 64-byte report beginning `81 05 00 21 01`. It waits for `02 22`, ignoring unrelated input reports until a deadline. For modern targets it first obtains the firmware product ID with read-only class-05 command `08`; that response must have application framing and agree with the selected target. Neither query retries or changes operating mode. Only explicitly listed USB personalities can use these queries.

The version is a little-endian 16-bit decimal-hundredths value at byte 2, with beta information at byte 4. Other fields are model-specific: byte 6 can be an edition, not a product ID. A live `6013` reply confirmed version 1.06 with zero at the formerly suspected product-ID position. Fields after the version are **not** reported as hardware revisions. The caller must still choose a HID interface whose report descriptor supports output `81` and input `02`.

The first-generation `3109` identity has additional flags and opaque trailing data. The vendor's legacy byte-offset logic did not produce a credible target on the live device, so only its firmware version is exposed; `target_verified` is false and the product ID remains the ambiguous runtime `3109`. Its version reply was 1.03. No trailing unique data is retained. The shared bootloader PID `3208` is deliberately not an identity-query target in this module.

`query_paired_identity` sends `81 04 21 01 00 00 06` with zero padding to a `6013` receiver. A matching class-04 reply provides the paired controller's version, beta, battery percentage, and edition. It provides **no firmware product ID**, so this information is for display and does not qualify the wireless controller for installation. A timeout is reported as no response, without claiming the controller is disconnected.

## Application update protocol

A request has report ID `81`, command class `05`, a 16-byte little-endian header (`command`, `parameters`, `length`, `CRC`, `total length`, `offset`), and at most 46 payload bytes. The first four fields are 16-bit integers and the last two are 32-bit integers. Data CRC is CRC-16/MODBUS, with initial value `FFFF` and reflected polynomial `A001`.

Application replies have report ID `02`, class `05`, and the same header layout. The first field is a status; the second echoes the command. Bootloader framing has an additional byte and is rejected. Read-only identity and product-ID checks precede any erase command, so incompatible framing must fail before a destructive operation.

The transaction follows these steps:

1. Require a qualification record bound to product ID, reported hardware revision (or an explicit unreported value), image revision, and exact image SHA-256. Recompute the image hash from its header and payload.
2. Restrict the image to the Ultimate 2 Telink layout: physical header address `5000`, bounded image length, and `KNLT` payload marker. Requery identity and verify the application-mode product ID.
3. For each block of at most 4096 bytes, erase with command `04` and write in at most 46-byte chunks with command `03`. Application-mode addresses start at logical zero, as in the vendor sequence. This must not be used as a bootloader write routine.
4. Compare the device's block CRC through command `C3`, then compare a complete byte-for-byte readback through command `05`. Correlate write and read acknowledgments with their addresses. These stricter checks must be confirmed against real target replies during qualification.
5. Only after every block verifies, save the image length/version with command `C4` and send reset command `07` once.
6. Return a result marked as requiring reconnect verification. The caller must reconnect to the same physical target and check the reported version before showing success.

No erase, write, commit, or reset is retried. An exception does not trigger a reset or a success result. Recovery must follow the device-specific procedure; the transaction does not invent rollback support.

The application owns image download/authentication policy, device locking, suspend inhibition, persistent job state, reconnect correlation, and the progress callback. A callback failure propagates, so the application should make status reporting reliable and retain the failed stage. A local SHA-256 binds bytes; it does not prove vendor authenticity.

## First hardware qualification

Before enabling a registry entry, record the physical model and hardware revision if available, controller versus receiver role, USB/HID descriptor, original version, official image URL and hashes, and the recovery procedure verified for that exact device. Capture an official successful update to establish application addressing, acknowledgments, checksum/readback behavior, commit semantics, and reconnect identity.

Compare that evidence with every native transaction stage. Exercise the native updater on an explicitly approved test target with stable USB and power. Record readback and the final reported version, and check normal controller operation afterward. Test failure handling with recorded or simulated traffic; destructive power-loss testing requires a recoverable test device and its own deliberate procedure.

Add a qualification record only for the tested model, role, hardware revision, and image. Do not generalize from the receiver to its controller, from one Ultimate generation to another, or from application mode to a shared bootloader. Unknown hardware revisions require evidence explaining how compatibility is otherwise established. Neither a checked UI box nor manually selecting a model can create a qualification record.
