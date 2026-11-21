# Wireless firmware update investigation

Investigated on 2026-11-21. **The Ultimate 2 Wireless controller has a real update channel through its USB receiver.** A read-only firmware identity query and application-memory read succeeded over that channel on Steam Deck. No firmware was erased, written, committed, or reset during this investigation. Native installation remains disabled.

## Live results

Two Ultimate 2 controllers connected wirelessly after being powered on. Their receivers changed USB personality from `6013` to `310b` while retaining internal firmware PID `6013`.

| Read-only check | Result |
| --- | --- |
| Paired-controller status through receiver 1.06 | Controller 1.10, stable, battery 100%, edition 1 |
| Paired-controller status through receiver 1.01 | Controller 1.10, stable, battery 100%, edition 1 |
| Radio updater PID through receiver 1.06 | Controller PID `6012`; direct receiver query returns `6013` |
| Radio read of 32 bytes at controller address `0x5000` | Exact match to the official controller 1.10 image header |
| Radio read of controller bootloader, addresses `0..0x4fff` | 20 KiB read successfully for private recovery analysis; version and battery status unchanged afterward |

No radio updater commands were sent through receiver firmware 1.01: its forwarding implementation has not been reviewed. Successful paired-status reads do not establish firmware-update compatibility. No Bluetooth connections, pairing changes, or vendor Bluetooth commands were made; the Bluetooth observations below came from cached services and static analysis.

The discovery fix uses the verified internal receiver identity to request paired status, instead of requiring the idle USB PID. Both wireless controllers now appear separately from their receivers in the device list.

## Reproducible identity probe

```sh
python3 -m eightbitdo probe-wireless
```

This command automatically finds candidate receivers, rechecks their identity, and only sends the radio PID query for stable receiver 1.06 paired with stable controller 1.10. Other versions return an explanatory unsupported result. It does not expose general-purpose firmware memory reads, change mode, initialize an update, or make an installation eligible.

The radio request is the existing class-05 product-ID request with its class changed to `06`, padded to 64 bytes including report ID:

```text
81 06 08 00 00 00 04 00 00 00 04 00 00 00 00 00 00 00
```

The controller's response retains class `05`. The observed 20-byte prefix was:

```text
02 05 00 00 08 00 02 00 00 00 02 00 00 00 00 00 00 00 12 60
```

The parser requires successful status, command `08`, length and total length 2, offset 0, and PID `6012`. It reads paired status again afterward and rejects a changed version, beta, or edition. A timeout or malformed reply stops the probe without retrying or entering update mode. A direct receiver reply cannot substitute for a controller reply.

## Official desktop implementation

The [official Windows Ultimate Software V2 1.38 archive](https://support.8bitdo.com/bd-uploads/files/ultimate_soft/8BitDo_Ultimate_Software_V2_Windows_V1.38.zip) has SHA-256 `59d529aa5f3797547e6b940c330e4c09d70276a13d4b103db3fc211e8f986e47`. Its native `8BitDoAdvance.dll` has SHA-256 `d79a7a6027df8929c0b8ea8e5b6726aed106a0595f4c9fb2de16696e8852a310`. These files were inspected statically, not executed.

`UpdatePage::startUpdate_new` (managed RVA `0x1a060`) calls `setWirelessupdate()` when the selected product is Ultimate 2 and the connection identity indicates its receiver. `HIDManage::readGamePadNewVersion` sets that receiver flag from PID `6013`, clearing it for directly connected controller `6012`. The flag's name, `m_IDLE`, does not mean the controller is asleep here.

Native `setWirelessupdate` (VA `0x1042d5f0`) sets a flag read by the packet builder (VA `0x10412e20`). That builder switches outgoing updater packets from class `05` to `06`. Responses still use class `05`. The native Windows wrapper requests 65 output bytes including report ID; Linux's inspected interface accepts the 64-byte identity/read requests tested here. Full transfer behavior requires separate qualification.

The vendor's Ultimate 2 transfer branch starts erase/write/CRC offsets at zero and reads payload bytes after the 28-byte DAT header. It uses 4,096-byte blocks and at most 46 bytes per write. Its `C4` command sends payload length and version as two little-endian 32-bit values, followed by command `07` to reset. These facts establish intent and addressing, not successful installation by this project.

## Firmware routing and staging

The reviewed official images are:

- [Controller 1.10, PID `6012`](https://support.8bitdo.com/bd-uploads/files/2026-05/dce4031137ac325384afbc6c3ba2f269.dat), SHA-256 `968edee528c23d56e2059288f90c109fa5135f060de5b403e72d0b6c50f5eb87`.
- [Receiver 1.06, PID `6013`](https://support.8bitdo.com/bd-uploads/files/2025-09/7607115e6716732ae948b19514560cfe.dat), SHA-256 `1018a299f3662098fcd7d2a71789d1290bb7603dd1998fe03759c5643cd05401`.

Offsets in this section exclude the DAT header. Receiver dispatch at `0x6200` routes class `06` to `rfUpdateCMD` at `0x614c`. Controller radio dispatch routes class `06` to its updater at `0xbb90`, also used by direct USB class `05`. The controller's command `08` handler at `0xbb3c` builds a constant PID reply without a flash operation. Receiver forwarding updates RAM bookkeeping only for this query.

Commands `03` and `C3` also set a receiver update-state flag. **Do not use CRC command `C3` as a harmless connectivity probe.** Some controller erase/write error paths appear to send their failure acknowledgment to USB even for a radio request; a wireless host must stop on a bounded timeout and must not commit or blindly retry destructive commands.

Write, erase, and CRC commands add a model-specific staging base to their logical offsets. Command `05` instead reads an absolute flash address:

| Target | Staging base | Commit metadata sector |
| --- | --- | --- |
| Controller `6012`, firmware 1.10 | `0x32000` | `0x31000` |
| Receiver `6013`, firmware 1.06 | `0x13000` | `0x12000` |

Therefore readback must use `staging_base + logical_write_offset`. Adding the DAT image address `0x5000` again is incorrect for this vendor transfer branch. This investigation corrected that address handling in the disabled native backend and added regressions for both targets across multiple blocks.

The audit also found a remaining verification mismatch: the device CRC handler constructs a CRC16 plus a 32-bit additive checksum, while the experimental backend expects a two-byte CRC response. Response header lengths and partial-block checksum behavior still need reconciliation with the vendor implementation before any write qualification. No CRC command was sent during the live probes.

The application commit handlers persist a 52-byte record but do not themselves validate the complete staged image. A separate staging area alone does not prove interrupted-update recovery. The privately read bootloader is not redistributed with this project, and a firmware identity probe does not establish safe installation.

## Bootloader recovery findings

Private inspection of the live controller's bootloader established several recovery mechanisms in code:

- Metadata at `0x31000` is 52 bytes, with CRC-16/MODBUS over the first 48 bytes. Invalid metadata is rejected.
- The pending-update handler (`0x0c0c`) checks the pending marker and length. Copying (`0x0b5c`) checks the staged PID, leaves staging intact, and copies later sectors before copying the application's first sector last. The persistent pending marker remains until copying finishes, allowing a reboot to attempt copying again.
- The pending marker is cleared **before** the installed-image checksum check. The copy loop does not perform its own byte-for-byte readback. This is not proof of recovery from flash programming faults.
- Failed image validation can enter USB recovery (initialization `0x1548`, polling `0x2884`) with VID/PID `2dc8:3208`. Recovery flash operations reject addresses below `0x5000`, protecting the bootloader region. This recovery mode was not entered or tested on hardware.

The 1.10 payload fits between the installed base and the metadata/staging regions. A larger image must not inherit that assumption: image hash, length, and safe geometry belong in its qualification record. ROM flash primitives lie outside the read bootloader region, so their electrical failure behavior was not established.

These findings support recovery from ordinary power interruption during copying, but no power-loss test, firmware write, rollback, or USB rescue was performed. Both controllers already run the latest stable 1.10 release available from the inspected catalog. Reflashing them was unnecessary for confirming the radio path.

## Bluetooth possibilities

The Deck's cached BlueZ records for both Ultimate 2 Wireless controllers contain Telink OTA service `00010203-0405-0607-0809-0a0b0c0d1912` and characteristic `00010203-0405-0607-0809-0a0b0c0d2b12`. Firmware 1.10 contains those UUIDs and an actual OTA write callback. The [Telink-authored fwupd implementation](https://github.com/fwupd/fwupd/blob/main/plugins/telink-dfu/fu-telink-dfu-ble-device.c) uses that characteristic, but its generic implementation does not qualify this 8BitDo model.

The OTA characteristic's ordinary read has no custom callback and returns a one-byte value, not the firmware version. Its OTA version opcode invokes a callback whose registration has not been traced. No Bluetooth OTA opcode was sent. The [Ultimate 2 Wireless FAQ](https://support.8bitdo.com/faq/ultimate-2-wireless-controller.html) excludes mobile software support; Ultimate 2 Bluetooth is a different model.

For the original Ultimate Bluetooth 80NA, the [official legacy Android APK](https://download.8bitdo.com/Ultimate-Software/8BitDo-Ultimate-Software-for-Android.apk), version 1.4.0, contains historical BLE updater code and an explicit type-41 update screen. SHA-256: `2f612ee0cdd8586b1a14e85171d857969b2ad0e4273390fea6f2bd03615e3eb1`. Advertised names `80NA`/`80NA_S` map to PID `6007` and configuration service `ff10`; the bootloader uses name `8bt` and service `ff20`. Current update-screen reachability and recovery have not been established. Those artifacts do not yet justify a Bluetooth firmware write.

## Qualification still required

Before enabling wireless installation, validate the statically identified bootloader recovery paths on hardware and account for interrupted metadata/copy behavior. Then validate the full radio transfer, error handling, commit, reconnect identity, and normal controller operation. Keep receiver and controller firmware separate, and do not extend results to another receiver version or controller family without evidence.
