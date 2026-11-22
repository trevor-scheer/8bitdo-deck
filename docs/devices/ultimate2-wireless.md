# Ultimate 2 Wireless controller over 2.4 GHz

The source review covers the **Ultimate 2 Wireless controller, stable 1.09 → the exact official 1.10 image, through a stable 1.06 Ultimate 2 USB receiver**. This is experimental installation evidence, not a completed native update or a hardware qualification. The controller's USB recovery route also needs hardware validation. Bluetooth firmware installation is outside this scope.

The two controllers observed on Steam Deck already run 1.10. The official catalog checked on 2026-11-22 lists stable 1.10 and 1.09. Reinstalling or downgrading them is unnecessary to establish identity, inspect installed code, or check an unchanged-firmware USB recovery round trip.

## Target and identity

| Field | Reviewed finding |
| --- | --- |
| Commercial model | Ultimate 2 Wireless controller; not the separate Ultimate 2 Bluetooth model |
| Firmware target / catalog type | Controller `6012` / type `94`; receiver `6013` / type `95` |
| Connection | Controller paired over 2.4 GHz to its USB-connected receiver |
| Receiver USB personalities | `2dc8:6013` while idle and `2dc8:310b` while paired; internal target PID remains `6013` |
| Application HID framing | 64-byte reports including output ID `81` or input ID `02`; verify the opened vendor interface's descriptor |
| Controller edition | Edition 1 observed; this is not a hardware revision |
| Hardware revision and physical model code | Not established by the reviewed identity replies |
| Controller USB recovery | Static descriptor VID/PID `2dc8:3208`; query firmware PID `6012` and fingerprint boot bytes because this USB PID is shared |
| Hardware evidence | Steam Deck identity and bounded existing-data reads on 2026-11-21; no completed controller update |

Bind each job to the selected receiver's physical USB port, hold its device lock, and recheck both component identities. A receiver version reply is not the paired controller's version. Read-only preparation and the install worker must both verify the exact installed code. An unreported board revision cannot be inferred from an edition or shared USB PID.

## Official artifacts and fingerprints

The controller 1.09 source was retrieved from the official catalog on 2026-11-22. The controller 1.10 and receiver 1.06 artifacts were reviewed on 2026-11-21. DAT hashes cover the complete download. Payload hashes exclude the 28-byte DAT header.

| Artifact | Length | SHA-256 |
| --- | ---: | --- |
| [Controller 1.09 DAT](https://support.8bitdo.com/bd-uploads/files/2026-01/f49da7b0ff8bb0747c17cd624866216b.dat) | 145436 | `f754df15d0a3fde833f231c7d9d585490e22adfe9145a8dc1b7c85ab5515cbc1` |
| Controller 1.09 payload | 145408 | `6a2ff4de75982f398a2d19a57f1c43f1bda98e4154f1e3b9686ee41678c7b3a1` |
| [Controller 1.10 DAT](https://support.8bitdo.com/bd-uploads/files/2026-05/dce4031137ac325384afbc6c3ba2f269.dat) | 145436 | `968edee528c23d56e2059288f90c109fa5135f060de5b403e72d0b6c50f5eb87` |
| Controller 1.10 payload | 145408 | `43d8cd222586f8a118d867cb60b0711dee504aed1354d548958d0e985dac30e7` |
| [Receiver 1.06 DAT](https://support.8bitdo.com/bd-uploads/files/2025-09/7607115e6716732ae948b19514560cfe.dat) | 40476 | `1018a299f3662098fcd7d2a71789d1290bb7603dd1998fe03759c5643cd05401` |
| Receiver 1.06 payload | 40448 | `e5bc101ea9b9f1c970a7fbffe4a92261400ad49cbdb58d4190d08a6fd404b3fe` |
| Privately read controller bootloader, address `0..0x4fff` | 20480 | `faab5fff75e88556749d15980f84926592fc04329da2866e9af36919af529fa5` |
| Privately read receiver 1.06 bootloader, address `0..0x4fff` | 20480 | `20b9904986708ffae77491630faa24a5fb98ef073cdb557488a82be05e9ce5e7` |

Require the controller bootloader fingerprint on the actual 1.09 source device. The private dump came from a controller running 1.10; the version number alone does not establish which bootloader a 1.09 device contains. Likewise, require the receiver bootloader and complete installed 1.06 payload, not just its reported version.

The [wireless investigation](../wireless-updates.md#official-desktop-implementation) records the official Windows Ultimate Software V2 1.38 archive, native library hashes, and host-side function offsets. The analysis used static disassembly and original descriptions. Proprietary firmware, updater binaries, boot dumps, and raw captures are not distributed here. SHA-256 binds reviewed bytes; it is not a vendor signature.

## Source compatibility

Controller 1.09's updater, checksum routines, reply builders, and dispatcher at payload offsets `0xb540..0xbbfe` match 1.10's `0xb568..0xbc26` in operations and constants. The differences are relocated branches and function calls. The flash primitives at `0x9690..0x999c` and RF response queue at `0x143c..0x148a` likewise retain their operations and constants. The radio dispatcher at `0xb88` passes route argument 1 to the respective updater.

This comparison supports 1.09 as an installation source, subject to live fingerprints. It also supports the existing non-mutating 1.10 inspection route. It does not authorize a downgrade, a different receiver source, another edition, or a future image merely because its advertised model matches. Future releases can be discovered from the catalog, but installation requires a reviewed source/image authorization with exact hashes and geometry.

## Transaction and geometry

The receiver forwards class-06 updater requests to the controller; responses retain class `05`. The [radio initialization findings](../wireless-updates.md#radio-initialization-and-idle-recovery) explain why `C1` has a header-only radio acknowledgment, why initialization changes flash protection state, and how receiver update state times out.

The reviewed transfer has these stages:

1. Verify receiver PID, stable 1.06, bootloader, and installed application. Verify paired controller PID, stable source version, edition, battery, complete bootloader, and source application. Reject pending or damaged controller commit metadata.
2. Send `C1` once. Require its exact header acknowledgment. Use source-reviewed geometry rather than absent radio geometry bytes.
3. Erase each 4096-byte staging sector with `04`. Write at most 46 payload bytes per `03` packet, starting at logical offset zero. Validate acknowledgments against the request.
4. Use `C3` to compare CRC16/MODBUS and additive32 over the firmware's complete 128-byte groups. Read every staged byte with absolute-address command `05`, including any checksum tail. Repeat full verification after all blocks are written.
5. Send `C4` once with payload length and DAT version as two little-endian 32-bit fields, length/total length 8, and offset zero. Send reset `07` once after the matching commit acknowledgment.
6. Verify receiver identity and paired controller destination version after reconnect. Keep success separate from merely sending reset. Check ordinary controller operation during hardware validation.

A timeout, malformed reply, checksum mismatch, or readback mismatch stops the transaction before any later commit. Do not automatically retry erase, write, commit, or reset. Some controller error paths reply through USB even for a radio request, so an RF timeout can represent a rejected command rather than a disconnected device.

| Region | Bounds for the reviewed 1.10 image |
| --- | --- |
| Installed application | Starts at `0x5000`; payload ends at exclusive `0x28800` |
| Installed copy sectors | 36 sectors; exclusive end `0x29000` |
| Commit metadata | 52-byte record in sector `0x31000` |
| Staging | Starts at `0x32000`; payload ends at exclusive `0x55800` |
| Staging sectors | 36 sectors; exclusive end `0x56000`; final unused bytes remain erased |
| Conservative maximum application size | `0x2c000`, keeping rounded installed bytes below metadata |

Erase, write, and application CRC use logical offsets relative to staging. Readback uses absolute `0x32000 + logical_offset`. The DAT address `0x5000` describes the installed destination and must not be added to staging offsets. The reviewed boot copy leaves staging intact, copies later application sectors first, and copies the first sector last.

## USB recovery and remaining validation

The directly connected controller accepts the special application report `81 05 00 51 00`, padded to 64 bytes, to request USB recovery. Its 1.10 handler at `0xc52c` calls `0xc51c`, stores `0x5a` in analog register 60, and resets. Boot code reads that register and enters USB recovery before processing pending metadata. This frame is not a radio updater command and must not be sent through a receiver. The analogous 1.09 handler has the same behavior at relocated offsets.

The bootloader uses different commands and lengths from the application updater. Its dispatcher has no `C1` or `C4` handler. Boot erase/write/read use absolute addresses; boot `C3` returns CRC16 over at most 256 bytes. Flash primitives reject addresses below `0x5000`. Boot reset clears the recovery marker before restarting. The shared USB PID cannot establish the correct component or authorize a write.

Pending metadata includes CRC16 over its first 48 bytes. The bootloader retains the pending record while copying and can restart that copy after a reboot. It clears the pending record before checking the installed image. There is no observed redundant metadata record or destination readback inside the copy loop. These mechanisms support a recovery design but do not establish successful recovery from a programming fault or interrupted power.

The next hardware evidence is a directly connected controller entering USB recovery, returning PID `6012`, matching its full bootloader and intact application, and returning to the same firmware after one reset. A complete wireless update, USB rescue installation, post-update input checks, and physical power-loss recovery have not been demonstrated. Until those results exist, keep installation experimental and report the exact recovery actions that have actually been tested.
