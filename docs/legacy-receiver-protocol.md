# Ultimate Bluetooth receiver protocol

`eightbitdo/legacy_protocol.py` implements the receiver-specific boot transaction
found in 8BitDo's desktop updater. Its offline tests establish packet encoding,
image offsets, operation ordering, and failure handling. They do not qualify
hardware. The service registry must separately establish a receiver's boot
transition, initialization tuple, recovery, and firmware compatibility before
enabling installation.

The original Ultimate Bluetooth receiver has firmware PID `6008`, catalog type
`42`, and model identifier `80NA_R`. Runtime USB PID `3109` is shared with other
receivers: the class-`22` reply's model tag `0023` identifies this receiver.
The application identity parser implements that distinction. Raw reply bytes
8–9 are not a reliable firmware PID for this device.

## Sources and reproducibility

The primary artifact is the [official Windows firmware updater archive](https://download.8bitdo.com/Tools/FirmwareUpdater/8BitDo_Firmware_Updater_Win.zip),
downloaded during the 2026-11-21 investigation. These hashes identify the exact
versions inspected:

| Artifact | SHA-256 |
| --- | --- |
| Windows ZIP | `88fd6af7eb62a33546415db75a40907ef9120d14f10c6cd7a4337ea59994a26f` |
| `8BitDo Firmware Updater.exe` | `5880c67c092f6d6e658f5a3a10c87dd42a439d561de75384599befe7b905916c` |
| `8BitDoFirmwareUpdaterTools.dll` | `b38ffa61b12af29d41dc55212e5ba44e509130facf6e3c4db9a7141aa313f974` |
| Receiver type 42 firmware 1.01 DAT | `6e5da88afe1c95c2810b8a47af18f9fa9c062667f02c9f1f0cb4a6fc00373e59` |
| Receiver type 42 firmware 1.02 DAT | `f1b536fe9f181a48bc6ac32a2b010e2f5187c6e8a918d4d2b088894e3cc422b0` |
| Receiver type 42 firmware 1.03 DAT | `d66aadc6cbadcf433dd5ea186665ddc5cb65ccd301709cc97b106a8a1363e65d` |
| Receiver type 42 firmware 1.04 DAT | `099101a6ca62321b76de2b7c61452342be794956a622aba542120125d91ffb97` |

Inspect the managed executable as .NET IL and the native DLL as x86 PE32 code.
Native addresses below are virtual addresses with image base `10000000`.
Firmware and vendor program code are not redistributed in this repository.
Tests contain synthetic data and packet fixtures derived from the protocol.

The optional managed `JLDll` implementation references a separate
`firmwareUpgradeTools.dll` and identifies types `42`, `43`, and `44` as its
family. The inspected archive omits that DLL; `isSupportJL` defaults to false
and has no assignment in the inspected managed code. This does not prevent a
native implementation: the shipped native DLL explicitly routes PID `6008`
through its HID receiver updater.

## Boot transition and report framing

Managed `DinputHid.DinputNewBoot`, RVA `6b5c`, changes byte 4 of
`changeDinput_CMD` from `02` to `00` and writes 64 bytes through
`WriteHidAdvance`. `UpdateView.dinputBoot` invokes it for runtime `3109`.
The output starts `81 05 00 51 00`; all remaining bytes are zero.

Native enumeration at `10004f4e`–`10004f9f` sets the boot transport flag for
VID `2dc8`, PID `3208` or `3002`. The update dispatcher at `100067c1` enters
`1000bd20` through that flag. These are supported backend interfaces; a
particular receiver's actual transition still needs observation on the same
physical USB port.

`100053a0` writes an API report selector of zero followed by a 64-byte
unnumbered output packet. The payload begins with class `05`, followed by
little-endian fields:

| Payload offset | Field |
| --- | --- |
| 0 | Class byte `05` |
| 1 | Command, 16 bits |
| 3 | Parameters, 16 bits, zero here |
| 5 | Length, 16 bits |
| 7 | CRC-16/MODBUS over supplied payload bytes |
| 9 | Total length, 32 bits |
| 13 | Relative offset, 32 bits |
| 17 | Command data, up to 46 bytes |

The CRC uses initial value `ffff` and reflected polynomial `a001`, as shown
by routine `10009b70` and its table at `101aee94`. PID `6008` uses CRC `ffff`
even for empty command data. A PID query requests two response bytes but
supplies no command data; its CRC is therefore also `ffff`.

On Linux, [hidraw writes include the report selector, while unnumbered reads do not](https://docs.kernel.org/hid/hidraw.html).
The backend sends 65 bytes and receives up to 64. Do not remove the first
byte of a received packet: the firmware acknowledgment itself starts `00 05`.
Status is the 16-bit field at received offset 2, echoed command is at offset 4,
and response data begins at offset 18. Native helper `100094d0` validates
status and command; it does not establish that the remaining header fields
echo the request. The parser therefore does not impose modern protocol echo
rules on these replies.

The exact nonflashing boot query is 65 bytes, padded with zeros:

```text
00 05 08 00 00 00 02 00 ff ff 02 00 00 00 00 00 00 00
```

`1000bf10`–`1000bf79` sends it; `1000c069` reads the returned PID as a
little-endian 16-bit value at received offset 18. The `6008` branch at
`1000c2e4` jumps through `1000d138` to receiver updater `1000a4e0`.

Reset routine `1000a0e0` constructs this 65-byte, zero-padded output:

```text
00 05 07 00 00 00 00 00 ff ff
```

The reset routine does not call initialization, erase, write, or save. The
backend queries PID `6008` before sending reset. A successful return to runtime
must be verified after the device disconnects; an acknowledgment alone is not
completion evidence.

Host code proves which commands the application sends. It does not prove the
device boot handler has no persistent side effects. Do not describe an
unobserved boot transition or recovery procedure as hardware validated.

## Firmware layout and complete update sequence

The 1.04 DAT has a 28-byte header: version `104`, destination `3000`, payload
length `40000`, PID `6008`, and three zero words. Its total length is 262172
bytes. The payload contains `80NA_R` at offset 16. This is not the modern
Ultimate 2 Telink `KNLT` image format.

Receiver routine `1000a4e0` implements this sequence:

1. Send `C1` with no command data. Read 16 response bytes. Native debug strings
   identify the four little-endian words as `upgrade_addr`, `upgrade_len`,
   `upgrade_eoffset`, and `flash_alignsize`. The host logs all four but uses
   only the first and fourth in this branch. The embedded bootloader analysis
   below establishes the default `C1` path as file metadata reads and RAM
   setup for the inspected binary.
2. Read and match the DAT firmware PID against the boot target. Skip
   `upgrade_addr` bytes at the beginning of the DAT payload. The remaining
   bytes start at relative receiver offset zero; the DAT destination field is
   not substituted for this offset. Native checks and arithmetic are at
   `1000aabe`–`1000abc2`.
3. Erase the destination in `flash_alignsize` units using command `04`, empty
   data, `total_length=flash_alignsize`, and increasing relative offsets.
   This loop begins at `1000ac87`. The implementation supports complete
   4096-byte pages only and rejects partial or unsupported geometry.
4. Write the selected DAT bytes in **32-byte chunks**, command `03`, with
   `total_length` equal to the remaining image length. Native file reads at
   `1000aec9` and `1000af39` establish the chunk size; `1000aef2` calls
   `1000a040`, which selects command `03` and uses packet builder `100091c0`.
5. Verify pages 2 through the final page with command `C3`. Each request
   contains three little-endian words `(relative_offset, 4096, 0)`;
   request length and total length are both 12, but the **header offset is
   zero**. Read a two-byte CRC and compare it with the same page of the
   selected DAT bytes. This loop starts at `1000b090`.
6. Send `C2`, with no command data, and require success (`1000b1e8`). Then
   verify page 0 and page 1 using the same `C3` format (`1000b39c` and
   `1000b5c6`). The native updater deliberately checks those pages after
   `C2`; treating this as modern `C4` finalization would change the protocol.
7. Send reset `07` only after every CRC matches (`1000b772`). Reconnect to
   the same physical receiver and verify the expected application version.

The backend requires an externally qualified initialization tuple and matches
all four returned words before erasing. It does not retry destructive commands
or restart the entire update after a failure. A failed verification stops
without an automatic commit or reset. A failure after `C2` is still reported
as a failure; successful activation has not been established.

## Embedded bootloader evidence

The official [1.01 factory image](https://support.8bitdo.com/bd-uploads/files/old/25f612db-9d41-4c6b-81ad-f15b6b98861a.dat),
[1.02 image](https://support.8bitdo.com/bd-uploads/files/old/fb73dec1-db10-42eb-a625-ac15cbde47b8.dat),
[1.03 image](https://support.8bitdo.com/bd-uploads/files/old/9ae69ca3-4fa5-4d5f-9208-b0a74f902f28.dat)
and [1.04 image](https://support.8bitdo.com/bd-uploads/files/old/a97e7f58-d56e-4cc2-956a-d864974f9745.dat)
have identical first `4000` bytes after the DAT header. Static extraction finds
Jieli filesystem version 2, PID `80NA_R`, 4096-byte alignment, `uboot.boot` at
`00a0`, `isd_config.ini` at `36f0`, and `app_dir_head` at `4000` with length
`ffffffff`. Application writes therefore start after the bootloader and top
filesystem directory. The fixture tuple `(4000, ffffffff, 0, 1000)` is derived
from those metadata and code paths; it has not been recorded from hardware.

For reproducibility, remove the 28-byte DAT header and run
`firmware/fwunpack_newfw.py`, then `firmware/debank.py` on `top/uboot.boot`,
from [jl-misctools](https://github.com/kagaimiq/jl-misctools) commit
`0a5b12db0ef38f3042acffbe2452730a37fd2405`. The unpacker checks filesystem and
bank CRCs. The resulting bank has 13888 bytes, load address `4000`, and SHA-256
`054d5e738113474f321e4ba985265a8f23e897b6a98646a7d8398ff94217e915`.
The [PI32v2 SLEIGH definitions](https://github.com/quarkslab/ghidra-jieli) at
commit `e1bd0707874b77b759401555d24839ad43af1267` permit static disassembly.
Some stack-mask instructions need manual decoding; parallel operations use
register values before the paired instruction. Addresses below are bank
addresses, not Windows DLL addresses.

| Boot address | Established behavior |
| --- | --- |
| `5d2a`–`5d50` | Command `08` returns hardcoded target `6008`. |
| `5d5e`, `5e54`, `603c` | Default `C1` opens `app_dir_head` in mode zero and returns its metadata plus flash alignment. |
| `4b98`–`4dfc` | Mode-zero file open reads, decrypts, and validates filesystem headers; updates a RAM file handle. No flash erase/program path is called. |
| `5d82`–`5df0` | Command `04` erases the selected relative page. Offset zero also clears the RAM header buffer and count. |
| `5df4`–`5e52`, `6112` | Command `03` checks data CRC, buffers offsets below `1000` in RAM, and programs subsequent bytes through the file handle. |
| `5ee0`–`5f2e` | `C2` programs the buffered first 4096-byte page. This explains why its verification follows `C2`. |
| `5f36`–`5ffa` | `C3` reads the relative range and returns CRC-16. |
| `600c`–`603a` | Command `07` acknowledges, changes a hardware register and RAM exit flag; no initialization, erase, or save call. |

The bootloader startup is **not universally read-only**. At `595c`, a boot
base of `10000` enters a migration path that erases and copies boot data
(`598c`, `599e`, `59b6`). The initial base comes from a ROM/caller argument
captured at `6f62`. The official images have base zero, which is consistent
with skipping migration, but an image file does not establish the live ROM
argument. Do not classify every application-to-boot transition as having no
persistent side effects.

Startup checks the application filesystem header. An invalid application
header can route through `58da` to reopening the preserved top-level
`app_dir_head` and entering the USB update loop. Destroying the top-level
directory instead reaches another fallback loader, whose recovery behavior is
not established here. This supports the purpose of buffering the first app
page, but does not demonstrate recovery from arbitrary interrupted writes or
corruption after a valid header is committed.

All four cataloged releases, including factory version 1.01, contain the same
packaged bootloader. This covers the known factory/update history, but is not
a readback of the installed receiver. Application updates skip the boot
prefix. No recovery after erase or power loss has been performed on the
user's receiver.

### Application boot-entry follow-up

Static inspection of the extracted applications narrows the entry path but
**does not eliminate the migration exception**. The version 1.03 application
SHA-256 is `41cb3846b0005d29f69880f87603f43111ed196bbaa2dd8b7cc1177d5550b7d3`;
version 1.04 is `a21c0fa6e0d35fe0933dbc4cc95f9cd789f6db2b8c7c29c7b3b23c67e1e874da`.
These are hashes of statically extracted `app.bin`, distinct from DAT hashes.

| Application | Command `51` dispatch | Final entry routine |
| --- | --- | --- |
| 1.03 | `01e017d8`–`01e017de`, parameter handler `01e00e3a` | `01e0385c`–`01e03872` |
| 1.04 | `01e018ac`–`01e018b2`, parameter handler `01e00f0a` | `01e038b6`–`01e038cc` |

The parameter-zero branch sets the application upgrade flag in RAM. The final
entry routine copies eight bytes, literal `ubootZst`, from initialized RAM
`2af1` to retained RAM `10800`, then invokes the chip-reset path through code
copied into RAM. It does not directly call the bootloader with a chosen boot
base. The bootloader's `4dfe` routine checks and clears that same retained-RAM
magic. The inspected Jieli BR23 linker definitions independently name
`10800` as `nvram_list` and `1127a8` as `memcpy`.

The vendor parser's visible application caller uses an internal `66 aa`
envelope. The normalization of the complete USB report `81 05 00 51 00` into
that envelope was not fully traced. The desktop updater independently proves
which USB report it sends; the application findings do not constitute a
complete proof of every intervening path or side effect.

Even if that linkage is completed, the identified reset routine does not
establish the subsequent ROM-supplied `[r0+4]` boot-base argument. ROM selection
and the installed flash layout remain outside this trace. Identical official
boot prefixes and the zero-based packaged layout are compatibility evidence,
not proof that the live migration guard is false. This follow-up does not
justify marking boot entry read-only or enabling a write authorization.

## Experimental authorization and connection handoff

`LegacySourceReviewedAuthorization` is distinct from `FlashQualification`.
It binds an exact DAT SHA-256, permitted source versions, hardware/image
revision, immutable expected initialization tuple, and boot-prefix SHA-256 and
CRC-16/MODBUS. Its evidence level is `source-reviewed-experimental`. Neither a
catalog result nor a UI-selected model may construct a record.

For the four official releases, the first `4000` payload bytes have SHA-256
`4f9f771cd34c7147a9123c35f2aac3d397396071c58c9f6571b9efccfc64dc81`
and CRC-16/MODBUS `1ecd`. These checks identify the reviewed packaged artifact;
they do not read or authenticate the installed receiver's bootloader.
`validate_receiver_authorization` performs these checks before any device
operation. The backend repeats them and requires the actual `C1` response to
match all four authorized initialization fields before erase.

An initial user-run update can therefore be authorized explicitly as an
experimental source-reviewed transaction, without inventing a previous
successful flash. The service must preserve that label and disclose its
remaining hardware/recovery limits. No enabled authorization record is
provided by this backend. Boot entry belongs only to the confirmed update
transaction because of the conditional migration behavior described above;
it is not part of ordinary discovery or a supposedly read-only preview.

`connection.require_boot_access` checks setup and runtime access before entry.
`wait_for_boot` opens only a boot interface on the original physical USB port,
using the transport's fd-based USB and descriptor checks. Shared boot USB
PID `3208` or `3002` never supplies a firmware identity. The backend must still
query its target. `wait_for_application` checks that same port's verified
runtime target and exact stable firmware version after reset. A timeout or
wrong identity cannot mark an update complete.

## Qualification still required

Before the service enables a receiver update, record the actual runtime-to-boot
transition, descriptor, target reply, and return to runtime. Separately
establish the live initialization tuple, bootloader compatibility, power-loss
recovery, and the intended firmware/version combination. Static analysis of
`C1` and `C2` does not replace those hardware checks.

The desktop `UpdatePrompt.changeUIAndTest` (managed RVA `1b3fc`, IL offsets
`5eb`–`629`) groups types `35`, `42`, and `44` and displays only the instruction
to insert the receiver. Its second and third instruction labels are empty.
The generic instruction to hold PAIR while reconnecting applies to other types;
it is not evidence of this receiver's recovery procedure.

The inspected [official web updater](https://web.8bitdo.com/) has `6008` in
its catalog but excludes it from its update routing arrays. Its `3109` PID
parser also reads a different field from the desktop's verified model tag.
Catalog presence and a chooser filter do not qualify the browser as a receiver
update fallback.
