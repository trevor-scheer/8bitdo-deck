---
name: add-8bitdo-device
description: Research, implement, validate, and prepare upstream support for an additional 8BitDo controller, receiver, or accessory in 8BitDo Deck. Also use when extending an existing family's reviewed firmware versions. Covers discovery-only contributions through explicitly authorized hardware update validation.
---

# Add 8BitDo device support

Turn evidence about a specific device into a reviewable contribution. Work in this repository; run commands from its root. Read its `AGENTS.md` instructions if present. Use an isolated worktree for nontrivial edits and preserve unrelated work.

This skill does not grant permission to flash hardware, change its mode, capture an official update, publish a branch, or merge a contribution. Honor authorization already given for the concrete action; do not ask again when it covers the proposed operation. Research, implementation, and simulated validation can proceed while hardware access or permission is missing. Do not claim full support when only discovery is established.

## 1. Establish the target and outcome

Inspect the existing implementation and evidence before asking for missing information:

- [Support matrix](../../../README.md) and [protocol requirements](../../../docs/protocol.md).
- `eightbitdo/registry.py`, `discovery.py`, and `qualifications.py` for known models, USB personalities, and permitted source/image combinations.
- [Catalog policy](../../../docs/catalog.md) for firmware provenance and parsing.

Record the exact commercial name, label/model code, controller/receiver/accessory role, connection modes, firmware version and beta status, hardware revision if actually reported, and available test hardware/host. A dock and its removable dongle are not automatically two firmware targets. Treat Bluetooth, 2.4 GHz, USB, and each operating mode separately until evidence connects them.

Choose the contribution's achievable level:

| Level | Required result |
| --- | --- |
| Evidence only | Sanitized unresolved observations, missing evidence, and useful regression coverage; no new model or compatibility claim. |
| Discovery/catalog | Correct identification and firmware browsing; unsupported writes remain blocked. |
| Experimental installation | Source-reviewed transaction, narrowly bound authorization, explicit evidence/limitations, and device-specific recovery handling. A successful prior flash is not invented as a prerequisite for the first experiment. |
| Hardware-validated installation | A successful native update and reconnect/function checks on the exact recorded target/version combination, with all supporting evidence required by `docs/protocol.md`. |

Start an evidence note using [the evidence template](references/evidence-template.md), normally at `docs/devices/MODEL_ID.md`. Replace `MODEL_ID` with the repository model identifier. If the model is unresolved, use a descriptive observation filename such as `unresolved-usb-3109.md`; do not invent a registry model ID to name the note. Mark unknown and untested fields explicitly. Keep raw research artifacts outside the repository.

## 2. Discover without guessing commands

Start with passive OS information: `lsusb`, the selected USB device's sysfs attributes, and its HID report descriptors. Determine which interfaces carry vendor control reports versus game input. Record VID/PID, descriptor hash/layout, report IDs, report sizes, and relevant interfaces without publishing serial numbers or physical USB paths.

`python3 -m eightbitdo scan` is an **active identity probe** for already allowlisted personalities. Read the relevant query path before using it on a newly observed variant. Do not broaden query allowlists just because a device uses vendor ID `2dc8`, resembles an existing model, or shares a PID. Keep unknown devices visible and unresolved.

Derive identity queries from primary evidence, then test parsers offline before sending them. Distinguish USB PID, firmware target PID, catalog type, edition/model tag, and hardware revision. Shared runtime and boot PIDs cannot establish a firmware target. Manual model selection cannot resolve that ambiguity or authorize writes.

Add discovery/identity fixtures for valid, ambiguous, truncated, and wrong-target replies. Preserve a sanitized observed prefix where useful; use synthetic bytes for unrelated fields. An unknown revision stays unknown rather than being inferred from an edition byte.

## 3. Research the official route and firmware

Find the official product/support page and the updater that actually routes this component. Check the current vendor web assets or desktop updater; an entry in the catalog or USB chooser is not proof of an update implementation. Record source URLs, retrieval dates, updater versions, hashes, and relevant function names/offsets. Do not rely on stale asset filenames in existing research notes.

Inspect primary sources statically before live probing. When a capture is needed, use a known official updater and an owner-approved update on the exact test device. Record the starting version first: an official update may remove the source version needed for a native test. Do not downgrade to recreate it without a separate reviewed procedure and authorization. Keep vendor binaries, firmware dumps, full captures, and unique identifiers private; contribute original descriptions and minimal sanitized fixtures. Check licenses before reusing third-party code.

Map the catalog type and official stable releases to the component. Reuse `Catalog` for bounded HTTPS downloads, redirect/host validation, size checks, caching, and receipts. Parse the actual container, target, revision, version encoding, image extent, and embedded markers; do not assume every model uses the existing DAT layout. A locally computed SHA-256 binds bytes but is not a vendor signature. Do not reinterpret an undocumented vendor checksum to bypass a mismatch.

For an existing family/new release, compare the new image and the proposed source version against the reviewed layout, boot code, update handler, and recovery assumptions. Reusing a transport does not automatically approve the release or another starting version.

## 4. Specify the transaction before enabling it

Read only the relevant family references: [modern USB protocol](../../../docs/protocol.md), [original receiver protocol](../../../docs/legacy-receiver-protocol.md), [receiver recovery](../../../docs/receiver-recovery.md), or [wireless route](../../../docs/wireless-updates.md). Existing implementations are examples, not universal command specifications. Consider the trusted `fwupd` path when it already supports the exact target.

Document framing, endianness, command-specific reply sizes, acknowledgment correlation, timeouts, address units, erase geometry, preserved regions, chunk sizes, checksums/readback, commit semantics, boot entry, reset, and reconnect identity. Distinguish application and bootloader packets, including hidraw's synthetic zero output report selector for unnumbered reports. Trace error behavior and persistent side effects. A command named “read,” “initialize,” or “reset” may mutate state or activate firmware; radio forwarding can add side effects absent from direct USB.

Resolve any uncertainty that could select the wrong target, erase protected data, commit unchecked bytes, or strand the device before enabling that operation. If evidence is incomplete, upstream discovery/catalog or a disabled backend with an exact account of what remains unverified. Do not brute-force commands or automatically explore unknown memory ranges on hardware.

## 5. Implement the smallest supported path

Use the repository's existing boundaries:

| Concern | Files to inspect/change as needed |
| --- | --- |
| Model and component catalog | `eightbitdo/registry.py`, `catalog.py` |
| Discovery and identity | `eightbitdo/discovery.py`, `protocol.py`, `transport.py` |
| Family-specific transactions | `protocol.py`, `legacy_protocol.py`, `wireless.py`, or a separate module for incompatible semantics |
| Reconnect and recovery | `connection.py`, `recovery.py`, `access.py` |
| Durable jobs and workers | `service.py`, `jobs.py`, `worker.py` |
| Reviewed write policy | `qualifications.py` and the applicable authorization type |
| Contributor/user behavior | `src/api.ts`, `src/index.tsx`, `main.py`, `cli.py`, support and install docs |

Do not extend receiver-specific service/recovery allowlists to a controller without implementing and testing that controller's policy. For native implementations, preserve these invariants (the existing `fwupd` route instead relies on its supported-device and trusted-release policy):

- Match the opened HID descriptor, USB identity, and original physical port. Requery the firmware target before mutation and require stable source-version/revision compatibility.
- Validate the unchanged cached image and exact compiled authorization again in the worker. UI choices and downloaded metadata cannot create authorizations. Preserve the experimental versus hardware-validated evidence label.
- Hold job/device locks, retain source context durably before mutation, and prevent suspend using the supported user-session worker. Progress-reporting errors must not interrupt partially erased hardware; throttle persisted progress.
- Fail before erase on incompatible geometry or target. Verify the required bytes/checksums before commit, and verify the same physical target and destination version after reconnect before reporting success.
- Never automatically retry erase, write, commit, or reset, or invent rollback. Define recovery for a lost worker, partial image, pending commit, and failed reconnect. A recovery check must not flash or silently change mode; any subsequent mutation needs a distinct, explicit confirmation and fresh checks.
- Keep the plugin unprivileged. Use verified existing device access or a narrowly scoped permission mechanism; do not disable SteamOS protections or grant access to all HID devices.

Extend an authorization class only if its existing fields and checks genuinely describe the new family. A `FlashQualification` requires an observed successful native update and the supporting identity, official-transaction, and recovery evidence specified in `docs/protocol.md`. For a first experimental update, use a narrowly scoped source-reviewed record with the evidence required by its backend, including exact source versions and image hash. Do not fabricate installed-code fingerprints or treat two unreported revisions as a verified board revision. Do not remove authorization gates to make a test possible.

## 6. Validate in increasing order of risk

Follow [the validation procedure](references/validation.md): offline tests first, then evidence-backed identity probes, then a separately assessed boot round trip if needed, and finally an explicitly authorized installation. Validate each controller/receiver and each USB/radio route independently. Lack of hardware is a reason to limit the support claim, not to invent results.

Add meaningful regression coverage beside the relevant existing tests. Exercise wrong target/source/image, ambiguous identity, malformed replies, timeouts/disconnects, checksum/readback failures, geometry bounds, duplicate/unsupported actions, cache changes, and same-port reconnect/recovery. Assert that failures send no later destructive commands. Use simulated failures rather than unplugging a device during an update by default.

Run the affected tests while iterating. Before submission, run the repository's [Checks workflow](../../../.github/workflows/checks.yml) locally where possible:

```sh
python3 -m unittest discover -s tests -v
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
pnpm run test:ui
python3 scripts/package.py
git diff --check
```

Use Python 3.11 or later, the Node version in the workflow, and the pnpm version in `package.json`. Report failures and skips accurately. If TLS fixture tests skip because the interpreter lacks a trust store, configure the platform's trusted CA bundle; do not disable certificate verification. Package/import tests establish packaging, not hardware compatibility.

## 7. Prepare the upstream contribution

Update the support matrix, relevant install/recovery instructions, and evidence note to match the implemented level. Keep controller and receiver versions separate. Include the tested source → destination pair, model/revision scope, official artifacts/hashes, command trace summary, checks run, recovery behavior, and untested cases. Link evidence to exact implementation and tests. Do not bundle proprietary firmware or raw device dumps in commits, fixtures, release assets, or PR attachments.

Review the diff and staged files for unrelated changes and private data. Make cohesive commits separating discovery/catalog, protocol/service behavior, and enabling an authorization when practical. Follow repository branch/commit conventions. No plugin version bump, tag, or release is required just to propose device support unless the repository requests it.

If the contributor asked to submit upstream and repository access permits it, push the contribution branch (to their fork if needed) and open a PR against the intended upstream branch. Use available GitHub tools or the CLI; do not depend on a maintainer's personal skills or device aliases. A PR description should state the exact newly supported scope, evidence level, validation, and remaining limits. Watch CI and fix failures. Do not merge or publish a release as part of submission unless separately requested.

If submission or hardware work cannot proceed, finish the reviewable local contribution and give the precise missing access, evidence, or hardware action. End with the branch/PR, achieved support level, tests, and remaining validation; do not present a partial route as universal controller support.
