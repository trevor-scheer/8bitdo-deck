# Firmware catalog

The backend uses the public endpoint used by the official [web updater](https://web.8bitdo.com/): an HTTPS JSON `POST` to `https://support.8bitdo.com/restapi/link/v1/client/firmware/select` with `firmware_type` and `beta` fields. The endpoint required no authentication during inspection on 2026-11-21. It is an undocumented vendor interface and can change.

`Catalog.list_releases()` requests stable releases by default. Each entry retains its target catalog type, release key, version, size, release notes, source URL, and vendor MD5 field. A response for a different target type is rejected. Controller and receiver types are separate; a catalog lookup does not establish connected-device compatibility.

Downloads require HTTPS with certificate validation and exact vendor hosts. Every redirect is checked, metadata and firmware have size limits, and network operations have timeouts. Complete files are checked against the declared size, assigned a local SHA-256 digest, and atomically cached under a content-derived filename with a provenance receipt. A supplied independently trusted SHA-256 digest must match. User-facing callers should choose the cache directory themselves and never accept a destination path from frontend input.

## Verification limits

The vendor `md5` field has unverified semantics. On 2026-11-21, its value differed from the complete downloaded `.dat` file for all five sampled targets: Ultimate 2 controller and receiver (types 94 and 95), Ultimate Bluetooth controller and receiver (41 and 42), and Ultimate 2.4G (43). The official web client checks the downloaded size but does not compare this field to a calculated file MD5. This project records `vendor_md5_matches` and explains a mismatch; it does not silently describe the field as a valid whole-file checksum.

HTTPS authenticates the download connection. A locally calculated SHA-256 identifies the received bytes and detects later changes; it does not establish an independent vendor signature. A cached artifact is **not approved for flashing**. The update engine must independently validate the firmware container, exact target model and component, protocol requirements, and hardware qualification before enabling a write.

Source evidence: the vendor's `request-ZgT0Hy98.js` defines the HTTPS API base, `connectDevice-CqNxlsyD.js` defines the select request, and `SelectFirmware-DUA2JDSO.js` consumes the release fields and checks file size. These assets were inspected from `https://web.8bitdo.com/assets/`; vendor implementation code is not included in this repository.
