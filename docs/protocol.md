# Native protocol implementation

The service integrates application-mode transactions for Ultimate 2 Wireless controllers (`6012`) and receivers (`6013`), plus the distinct original Ultimate Bluetooth receiver (`6008`) boot transaction. **One experimental native update is authorized:** stable Ultimate 2 receiver 1.01 to the exact reviewed official 1.06 image, with matching installed bootloader and application fingerprints. Native controller, wireless, and legacy installation remain blocked; no native transaction is hardware-qualified. Source review can authorize a narrowly scoped experimental transaction; it does not establish a successful hardware update. Simulated tests do not establish hardware compatibility.

## Evidence

The implementation was independently written from the observable wire format and these sources, inspected on 2026-11-21:

- The official [web updater's connection and update module](https://web.8bitdo.com/assets/connectDevice-CqNxlsyD.js) defines the 28-byte image header, application command framing, CRC algorithm, update sequence, and separate controller/receiver handling.
- The official [Windows updater](https://download.8bitdo.com/Tools/FirmwareUpdater/8BitDo_Firmware_Updater_Win.zip), statically inspected, defines legacy model discrimination in `DinputHid::getNewGamePadDetails`; see [archive hashes and cross-checks](research.md#desktop-identity-decoder).
- The official [main module](https://web.8bitdo.com/assets/index-BXAl9J5z.js) defines product/catalog identities and command constants.
- Static inspection of the official Ultimate Software V2 1.38 native DLL and controller 1.10 / receiver 1.06 firmware establishes response payload sizes, staging addresses, initialization, and commit fields. See the [artifact links, hashes, and routing evidence](wireless-updates.md#official-desktop-implementation).
- [Firmware analysis](https://github.com/ascendedent/8bitdo-Linux-Software/blob/main/docs/firmware.md) and [captured identify traffic](https://github.com/ascendedent/8bitdo-Linux-Software/blob/main/docs/capture-log.md) corroborate the identify command, report layout, and distinctions between USB personalities. This repository has no identified license; its implementation code was not copied.
- [Ultimate 2 configuration protocol](https://github.com/MiguelSanzBr/8bitdo-ultimate2-linux/blob/main/docs/PROTOCOL.md) reports hardware verification of version and paired-controller queries and distinguishes the edition field from a firmware product ID.

The published vendor code is evidence, not an imported dependency. It does not establish that every protocol path works on the connected hardware.

## Read-only operations

`query_identity` sends a 64-byte report beginning `81 05 00 21 01`. It waits for `02 22`, ignoring unrelated input reports until a deadline. For modern targets it first obtains the firmware product ID with read-only class-05 command `08`; that response must have application framing and agree with the selected target. Neither query retries or changes operating mode. Only explicitly listed USB personalities can use these queries.

The version is a little-endian 16-bit decimal-hundredths value at byte 2, with beta information at byte 4. Other fields are model-specific: byte 6 can be an edition, not a product ID. A live `6013` reply confirmed version 1.06 with zero at the formerly suspected product-ID position. Fields after the version are **not** reported as hardware revisions. The caller must still choose a HID interface whose report descriptor supports output `81` and input `02`.

The first-generation USB IDs `3109` and `6007` are shared personalities. The desktop updater uses the same zero-padded query above, with the report ID retained in the reply. The version is at raw bytes 2–3, beta is **only byte 4**, and the model tag is at bytes 6–7. Byte 5 contains flags. This backend recognizes:

| USB PID | Little-endian model tag | Firmware PID | Target |
| --- | --- | --- | --- |
| `3109` | `0023` | `6008` | Ultimate Bluetooth receiver |
| `3109` | `3013` or `9023` | `3013` | Ultimate 2.4G receiver |
| `6007` | `0022` | `6007` | Ultimate Bluetooth controller |
| `6007` | `0023` | `6008` | Ultimate Bluetooth receiver |

The vendor's `3109` decoder defaults unrecognized tags to the Bluetooth receiver. We require the observed `0023` tag, corroborated by its explicit `6007` receiver branch. We also require a zero high byte for the `6007` tags, which is stricter than the vendor's one-byte test. Unknown tags remain unresolved; they never inherit a firmware target from a USB ID or manual choice. Truncated tags are rejected.

The sanitized live prefix `02 22 67 00 00 80 23 00` identifies the Bluetooth receiver at version 1.03, beta 0. No trailing unique data is retained. The web updater's legacy path uses a different query header and cannot substitute for this desktop decoder. Class-05 command `08` is not sent to these legacy personalities. Shared bootloader PID `3208` is excluded from runtime identity queries; dedicated boot transports verify their own framing and firmware target. Model identification alone cannot authorize a flash operation.

`query_paired_identity` sends `81 04 21 01 00 00 06` with zero padding to a `6013` receiver. A matching class-04 reply provides the paired controller's version, beta, battery percentage, and edition. It provides **no firmware product ID**, so this information is for display and does not qualify the wireless controller for installation. A timeout is reported as no response, without claiming the controller is disconnected.

The optional [wireless identity probe](wireless-updates.md) verifies the radio updater route on receiver 1.06 / controller 1.10 without starting an update. The general paired-status query above remains display-only.

An additional manual check on receiver 1.06 read 128 existing staging bytes at absolute address `0x13000` through direct USB class-05 command `05`, then requested class-05 `C3` for logical offset 0 and length 128. Both returned checksums matched those bytes. The production `_verify_block` helper also passed its checksum and complete readback checks against those existing bytes, with identical receiver identity afterward. These checks sent no initialization, erase, write, commit, reset, or mode-change command. They did not use radio class `06`, whose `C3` forwarding changes receiver update state. The app does not expose this manual memory-read procedure.

## First-generation receiver backend

The Ultimate Bluetooth receiver (`6008`, catalog type 42) uses the separate, service-integrated `legacy_protocol.py` backend. Its official 1.04 DAT has destination `0x3000`, payload length `0x40000`, and an `80NA_R` identifier. It is not the Ultimate 2 Telink image layout.

The inspected [Windows updater](https://download.8bitdo.com/Tools/FirmwareUpdater/8BitDo_Firmware_Updater_Win.zip) includes a receiver-specific HID path: boot identity, `C1` initialization, erase/write/`C3` verification, `C2` finalization, and reset. File offsets depend on initialization data, so the modern transaction and its `C4` commit cannot substitute for this path. The service validates a `LegacySourceReviewedAuthorization` before entry, checks recovery access, and reopens the boot interface on the original physical USB port. The backend verifies the firmware PID and all four authorized initialization words before erase. Boot transition, recovery, and a complete update remain untested on this receiver. See the [legacy protocol and evidence limits](legacy-receiver-protocol.md).

The inspected web updater includes `6008` in catalog metadata but omits it from its update-routing lists. Its `3109` identity decoder also differs from the validated desktop decoder. It is not a verified browser fallback for this receiver. Detection and catalog access do not enable installation. Proprietary updater binaries and firmware are not redistributed with this project.

## Application update protocol

A request has report ID `81`, command class `05`, a 16-byte little-endian header (`command`, `parameters`, `length`, `CRC`, `total length`, `offset`), and at most 46 payload bytes. The first four fields are 16-bit integers and the last two are 32-bit integers. Data CRC is CRC-16/MODBUS, with initial value `FFFF` and reflected polynomial `A001`.

Application replies have report ID `02`, class `05`, and the same header layout. The first field is a status; the second echoes the command. The remaining fields usually echo the request, including its length and CRC. That length does **not** necessarily describe the returned payload, and the header CRC is not a checksum of the response data.

The parser uses these command-specific payload sizes:

| Command | Reply length field | Actual response payload |
| --- | --- | --- |
| `C1` initialization | 0 | 16 bytes: initial address 0 and block size 4096 at byte 12; the two middle words are ignored |
| `C3` verification | 12 | 6 bytes: little-endian CRC16 followed by a 32-bit additive checksum |
| `03`, `04`, `C4` acknowledgments | Echoed request length | No payload |
| `05` readback | Requested read length | Exactly that many bytes |
| `08` product ID | 2 or 4 | Product ID, with total length equal to the reply length |

The parser rejects oversized or truncated payloads and invalid `C1`/`C3` length fields. Each exchange checks status and command, then correlates the echoed length, CRC, total length, and offset. Product-ID responses have the explicit length exception above. This application parser rejects bootloader framing; dedicated boot backends handle it separately. Read-only identity and product-ID checks precede initialization and erase.

For each block of at most 4096 bytes, `C3` checks only complete 128-byte units: `floor(block_length / 128) * 128` bytes. The backend compares both checksums over that prefix, then reads back **every** byte in the block, including any remaining tail. It does not pad or discard firmware bytes to satisfy checksum alignment. A block shorter than 128 bytes has an empty checksum prefix and still receives complete readback verification.

The preflight also bounds the image after rounding its size up to a 4096-byte sector:

| Target | Maximum rounded payload | Boundary protected by this limit |
| --- | --- | --- |
| Controller `6012` | `0x2c000` | Installed image at `0x5000` ends before metadata at `0x31000` |
| Receiver `6013` | `0xc000` | Staging at `0x13000` ends at `0x1f000`; installed image stays below metadata at `0x12000` |

These are conservative bounds for the reviewed layouts, not qualification records.

The transaction follows these steps:

1. Require a developer-issued `FlashQualification` or `SourceReviewedAuthorization` bound to product ID, reported hardware revision (or an explicit unreported value), allowed stable source versions, image revision, and exact image SHA-256. Recompute the image hash from its header and payload. Beta sources are rejected.
2. Restrict the image to the Ultimate 2 Telink layout: physical header address `0x5000`, the model-specific size bound above, the `KNLT` payload marker, and a matching embedded product ID at payload bytes 28–29. Requery identity and verify the application-mode product ID.
3. Read the commit metadata before initialization. Accept an erased factory record; reject damaged metadata or a pending image that a new transfer would overwrite. For source-reviewed authorization, read and hash the complete reviewed bootloader and installed application regions. These fingerprints are checked during preparation and again immediately before the update.
4. Initialize with command `C1`, parameters 0, and length/total/offset 0. Require initial address 0 and block size 4096 in its response. Initialization belongs to the update transaction; it is not a read-only identity query.
5. For each block of at most 4096 bytes, erase with command `04` and write in at most 46-byte chunks with command `03`. Application-mode addresses start at logical zero, as in the vendor sequence. This must not be used as a bootloader write routine.
6. Compare both device checksums through command `C3`, then perform complete byte-for-byte readback through command `05` at the absolute staging address (`0x32000 + offset` for controllers, `0x13000 + offset` for receivers). Apply the prefix and tail rules above.
7. After all writes, repeat checksum and complete readback verification across every block. Only after both verification passes succeed, save the image length/version with command `C4`, payload length 8, total length 8, and offset 0. Send reset command `07` once.
8. Return a result marked as requiring reconnect verification. The caller must reconnect to the same physical target and check the reported version before showing success.

No erase, write, commit, or reset is retried. An exception does not trigger a reset or a success result. Recovery must follow the device-specific procedure; the transaction does not invent rollback support.

The application owns image download/authentication policy, device locking, suspend inhibition, persistent job state, reconnect correlation, and the progress callback. A callback failure propagates, so the application should make status reporting reliable and retain the failed stage. A local SHA-256 binds bytes; it does not prove vendor authenticity. Workers launch through transient systemd user services with suspend inhibition. A benign live `Service._spawn` check verified that context: the launched worker rejected an unconfirmed recovery job before hardware access. The first firmware installation remains user-confirmed and untested.

The job engine checks `eightbitdo/qualifications.py`. `SOURCE_REVIEWED_AUTHORIZATIONS` contains one receiver `6013` record for stable source version 101 and the exact official 1.06 image SHA-256 `1018a299f3662098fcd7d2a71789d1290bb7603dd1998fe03759c5643cd05401`. It requires the reviewed full `0x5000`-byte bootloader and `0x7500`-byte installed application fingerprints. The image geometry and boot checksum constraints were checked against the actual official DAT. `QUALIFICATIONS` and `LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS` remain empty.

The job engine rechecks the image digest, identity, source version, and authorization evidence level before installation. The opened HID node is bound to its original USB port; legacy boot handoff also checks the unnumbered 64-byte vendor report layout and firmware PID. The update lock persists through reconnect verification. Status persistence after writes start is best-effort so a full disk cannot abort a partially erased device. Neither UI input nor a catalog response can create an authorization.

Native installation requires recovery access. The backend recognizes the trusted SteamOS policy or the fallback scoped rules and installation receipt. Normal SteamOS needs no administrator setup. On other Linux systems, the [fallback installer](install.md#receiver-recovery-access) uses `pkexec` and limits its rules to hidraw devices `2dc8:3208` and `2dc8:3002`. Before legacy mode entry, the service checks policy and runtime access; the bounded boot wait checks actual permissions, physical port, and report layout when opening the new node. Access alone does not establish a successful recovery round trip.

Tests model the firmware's echoed headers, actual response payload sizes, 4096-byte erase sectors, checksum quantization, and absolute staging reads. They cover both models, partial blocks, malformed replies, corrupted readback, and stopping before commit or reset on failure. The receiver check corroborates the production verification helper on 128 existing bytes.

The Ultimate 2 receiver 1.01 completed a bounded recovery round trip with production helpers and a lock on its original USB port: boot entry, `wait_for_boot`, firmware PID `6013`, source and metadata inspection, one reset, and `wait_for_application` verification of stable 1.01. Its 29,952-byte application and 52-byte erased metadata record were unchanged afterward. The test used SteamOS’s existing policy without permission changes and sent no `C1`, erase, write, or commit. It does not validate pending-image copy, power-loss recovery, or a firmware update. See the [receiver recovery evidence](receiver-recovery.md#bounded-hardware-validation). The original Bluetooth receiver has not entered boot mode in these tests.

## Authorization and hardware qualification

`SourceReviewedAuthorization` records experimental evidence and binds the modern destination image to reviewed installed-application and bootloader fingerprints. `LegacySourceReviewedAuthorization` instead binds the original receiver image, packaged boot prefix, and initialization tuple; it does not claim to fingerprint the installed bootloader. These records are distinct from `FlashQualification`, which records a demonstrated hardware update. A first experimental update must preserve that evidence label and its recovery limits rather than inventing a previous successful flash.

Before adding a hardware qualification record, record the physical model and hardware revision if available, controller versus receiver role, USB/HID descriptor, original version, official image URL and hashes, and the recovery procedure verified for that exact device. Capture an official successful update to establish application addressing, acknowledgments, checksum/readback behavior, commit semantics, and reconnect identity.

Compare that evidence with every native transaction stage. Exercise the native updater on an explicitly approved test target with stable USB and power. Record readback and the final reported version, and check normal controller operation afterward. Test failure handling with recorded or simulated traffic; destructive power-loss testing requires a recoverable test device and its own deliberate procedure.

Add a qualification record only for the tested model, role, hardware revision, and image. Do not generalize from the receiver to its controller, from one Ultimate generation to another, or from application mode to a shared bootloader. Unknown hardware revisions require evidence explaining how compatibility is otherwise established. Neither a checked UI box nor manually selecting a model can create a qualification record.

Matching two `None` hardware revisions means both are unreported; it does not verify a board revision. The evidence must identify how the physical model and revision scope were established. Source firmware versions must be listed explicitly because application update behavior can change between versions.
