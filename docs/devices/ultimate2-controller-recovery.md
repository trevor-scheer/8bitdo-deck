# Ultimate 2 Wireless controller USB recovery

This is a source-reviewed, experimental backend for firmware target `6012`.
It is distinct from the receiver (`6013`) and the Ultimate 2 Bluetooth model.
No controller write authorization is enabled by this module. USB recovery
erase/program, pending-copy recovery, and power-loss behavior have not been
validated on hardware. Simulated tests establish host-side restrictions only.

The implementation is [controller_recovery.py](../../eightbitdo/controller_recovery.py),
with [simulated tests](../../tests/test_controller_recovery.py). It does not
request boot entry, pick a USB device, or implement frontend/service policy.

## Evidence and scope

Private analysis on 2026-11-22 reviewed the complete `0x5000`-byte controller
bootloader read on 2026-11-21, SHA-256
`faab5fff75e88556749d15980f84926592fc04329da2866e9af36919af529fa5`.
The helper pins that fingerprint because the boot command semantics below
are incompatible with the application updater and receiver recovery helper.
The bootloader and its disassembly are not distributed.

The exact official [controller 1.10 DAT](https://support.8bitdo.com/bd-uploads/files/2026-05/dce4031137ac325384afbc6c3ba2f269.dat)
has SHA-256 `968edee528c23d56e2059288f90c109fa5135f060de5b403e72d0b6c50f5eb87`.
Its payload is 145,408 bytes and passes both checksum checks below. Related
vendor software artifacts and application/radio routing are documented in
[wireless updates](../wireless-updates.md). A local hash binds reviewed bytes;
it is not a vendor signature. Hardware revision is unreported.

## Caller contract

All entry points require a compiled `SourceReviewedAuthorization` with target
`6012`, exact destination image hash/revision, reviewed source versions, and
complete bootloader/installed-source fingerprints. An authorization must not be
constructed from UI selections or catalog responses. Boot mode does not report
the former runtime version; interrupted jobs must preserve original source
identity and the original USB port before changing mode.

The caller must own the update/device locks, prevent suspend, keep USB power
stable, check recovery access, and bind the opened interface to the original
physical port and reviewed unnumbered 64-byte HID descriptor. Shared USB
`2dc8:3208` alone does not identify a controller. These helpers first query
firmware PID `6012`, then fingerprint its entire bootloader. No radio transport
may be substituted for this USB boot interface.

| Entry point | Effect |
| --- | --- |
| `inspect_controller_recovery(transport, image, authorization=...)` | Reads only; returns `return-installed`, `finish-pending`, or `blocked`, or raises on malformed/unreviewed state. |
| `finish_controller_recovery(transport, image, authorization=...)` | Repeats inspection and sends one reset if permitted. Pending-image reset causes vendor flash copying. |
| `inspect_controller_rescue(transport, image, authorization=...)` | Reads boot identity/fingerprint and metadata; reports `reflash` eligibility even for a corrupt application or CRC-invalid metadata. Never resets or writes. |
| `flash_controller_recovery(transport, image, authorization=..., confirmed=True)` | Rechecks policy/boot/metadata; explicitly erases and reinstalls the authorized application, verifies it, and resets once. |

All resets require subsequent same-port target/version verification by the
caller before reporting success. No command is automatically retried. A send
failure propagates even if a reset might have reached the device; reconnect
inspection must resolve that uncertainty. Firmware failures do not trigger an
automatic reset or rollback.

## Boot checksums and geometry

The installed application starts at `0x5000`, commit metadata occupies a sector
at `0x31000`, and radio/application staging begins at `0x32000`. The host limits
the rounded application size to `0x2c000`, so its erase range ends before
metadata. Inspection reads are bounded below `0x5e000`, which includes the
largest accepted staging extent. Rescue writes and erases are restricted to
`0x5000..0x30fff`; bootloader, metadata, staging, and other flash remain untouched.

The image must have the reviewed `KNLT` layout, embedded PID `6012`, checksum
flag 1, and a plausible version. Unlike receiver recovery, the controller
requires two components:

- Boot function `0x01e4` reads primary extent `L` from payload offset `0x18`.
  The additive sum of bytes `[0:L-4]` must equal the little-endian word at `L`.
- Functions `0x1988` and `0x1904` locate the secondary component at `L+4` and
  obtain its length `S` from payload offset `L+24`. Function `0x0274` sums
  its `S` bytes and compares the little-endian word immediately afterward.

The host requires aligned, positive, in-bounds lengths and validates both
checksums before any transport call. For official 1.10, `L=0x17a64`, secondary
start is `0x17a68`, and `S=0xbc44`. These additive checks detect corruption;
they do not authenticate the manufacturer.

## USB boot wire protocol

Boot dispatch is at `0x23d8`. USB payloads begin with class `05` followed by
the little-endian 16-byte header used elsewhere in this project. On hidraw,
an output additionally starts with a synthetic zero report selector: 65 host
bytes for a 64-byte USB payload. Input is exactly 64 bytes beginning `00 05`,
with status/command/header at offset 2 and data at offset 18.

Every exchange checks framing, status, command, length, CRC, total length, and
offset. Every receive has a fixed timeout: two seconds, or ten for erase.
Transfer loops are bounded by reviewed image size; no generic memory-writing
API is exposed.

| Command | Boot handler | Semantics used by this backend |
| --- | --- | --- |
| `08` | `0x1d38` | PID query; reply length/total 2, data `12 60`. |
| `05` | `0x221c` | Absolute flash read. Host requests at most 32 bytes; firmware caps at 46. Header fields echo. |
| `04` | `0x1fa0` | Absolute erase address; total length determines rounded 4096-byte sector count. Host requests one aligned sector, length 0, total 4096. Reply changes length and total to zero, preserving address/CRC. |
| `03` | `0x2084` | Absolute write with payload CRC16/MODBUS. Host writes at most 46 bytes, length/total equal chunk size. Reply echoes header fields. |
| `C3` | `0x22b0` | Payload `(absolute address, length, block size)` as three little-endian words. Boot reads at most 256 bytes and returns **only CRC16**, despite echoing declared length 12. Block-size word is not used by this handler; host sends 4096. |
| `07` | `0x2370` | Acknowledges, clears analog recovery flag 60, and resets. Host sends once, without depending on an acknowledgment before disconnect. |

CRC16 uses initial `ffff`, reflected polynomial `a001`. The `C3` request CRC
is the CRC of its 12 payload bytes and is echoed; the separate two-byte reply
payload is the flash CRC. There is no application-style additive checksum
in this boot reply. The dispatcher has no `C1` initialization or `C4` commit
handler. Reusing the application transfer routine would address the wrong
regions and parse acknowledgments incorrectly.

The flash wrappers (`0x5e4`, `0x604`) reject writes below `0x5000`. The host's
upper bound is much narrower than those wrappers. Flash electrical behavior
and external ROM primitives are not established by this source review.

## Recovery decisions

Inspection requires either erased metadata or a valid 52-byte record with
CRC16 over the first 48 bytes, stored as a 32-bit word at offset 48. A valid
pending marker `aeb14d6c` requires exact target version, staging address,
payload length, every staging byte, and erased padding through the final
4096-byte sector. Only then can `finish-pending` authorize a reset. The vendor
copy routine leaves staging intact and copies the application's first sector
last, but clears pending metadata before checking the installed checksums.
This is not proof of recovery from flash-programming faults.

Without pending metadata, reset-only inspection requires either the entire
reviewed source application or exact authorized target, plus both checksums.
An incomplete application does not authorize reset.

Explicit USB reinstall can handle an incomplete application when metadata is
erased, valid/nonpending, or CRC-invalid. For CRC-invalid metadata, the exact
reviewed loader at `0x0ae8` clears its RAM copy; `0x0c0c` therefore cannot
activate that record. Reinstall preserves the damaged bytes and requires them
unchanged before reset. The reset-only helper remains conservative and rejects
damaged metadata. A CRC-valid pending record blocks direct USB reinstall,
including when its staging contents are invalid; this backend does not erase
or invent repaired metadata.

The explicit reinstall sequence is:

1. Verify image/policy, boot PID/fingerprint, and metadata state before erasing.
2. Erase the application header sector; erase/write/verify later sectors first.
3. Write the header sector last. Compare each sector's sixteen 256-byte boot
   CRCs, then every byte through absolute readback, including erased padding.
4. Repeat complete CRC/readback verification after all programming. Recheck
   unchanged metadata and full bootloader fingerprint.
5. Reset once. Require the caller's same-port runtime identity/version check.

## Forced entry and remaining hardware validation

On 2026-11-22, a directly USB-connected controller running stable 1.10 passed
a bounded boot round trip using production inspection/reset helpers. The
66.12-second check verified the exact bootloader fingerprint, all 145,408
installed application bytes, and valid nonpending metadata; one reset returned
the controller to stable 1.10 on its original port. Full application and
metadata comparisons before/after were identical. No firmware erase, program,
initialization, or commit command was sent. This validates the software entry,
read/inspect/reset, and unchanged-source return path only.

A second bounded round trip on the same date validated boot command `C3`
against 256 existing application bytes at `0x5000`. Its CRC16 result, `0x5b27`,
matched an independent calculation from readback. The 66.47-second check
returned to stable 1.10 with all 145,408 application bytes and metadata
unchanged. A strict command wrapper allowed only identity, reads, this fixed
checksum range, and one reset; no erase, program, initialization, or commit
was permitted. This adds read-only checksum evidence, not a rescue-flash test.

The running controller accepts USB report prefix `81 05 00 51 00`, padded to
64 bytes. Application handler `0xc52c` sets analog flag 60 to `5a` and resets.
Boot main `0x1662..0x1674` branches to USB recovery before processing pending
metadata when that flag is set. This command must not be routed over radio.

Static analysis also identifies a physical boot gate: function `0x04b0`
requires row 2/column bits `18`, mapped to LB+RB by the application matrix.
Holding LB+RB while powering/connecting USB is a **source-derived recovery
candidate, not yet a verified instruction for users**. The gate executes
before application checksums. An erased/corrupt header can supply an enormous
unchecked primary extent to the vendor checksum loop; automatic recovery USB
enumeration after interrupted direct rescue is therefore not guaranteed.
Forced entry must be physically validated before promising easy USB rescue.

The source-review and simulated tests do not establish a successful flash,
electrical power-loss behavior, physical button entry, or normal controller
operation afterward. Record those results separately if performed. A bounded
entry/inspection/reset round trip validates only that path, not erase/write
recovery. Do not intentionally interrupt a hardware flash to test recovery
without a separately approved, recoverable test procedure.
