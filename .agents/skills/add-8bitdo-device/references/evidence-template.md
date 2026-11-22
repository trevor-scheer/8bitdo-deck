# Device evidence note

Copy the useful sections into `docs/devices/MODEL_ID.md` and replace the prompts with findings. For an unresolved model, use a descriptive observation filename without adding a registry ID. Use “unknown,” “not tested,” or “not applicable” with a reason where appropriate. Do not submit an unfilled template. Link sources and distinguish observed behavior, static inference, and simulated behavior.

## Support claim

State the achieved level: evidence only, discovery/catalog, source-reviewed experimental installation, or hardware-validated installation. List the exact model/component, source → destination versions, modes, and unsupported variants where established. An evidence-only contribution must not claim a resolved model or compatible update. Explain how an unreported hardware revision is constrained, if applicable.

## Target and discovery

| Field | Finding and evidence |
| --- | --- |
| Commercial name and physical model code | Record the label without unique identifiers. |
| Component | Controller, receiver, adapter, or independently verified dock target. |
| Hardware revision | Reported value and source, or unreported. |
| Host/test date | OS, relevant software versions, and date. |
| Connection/operating modes | USB, Bluetooth, or radio; record mode-specific identities. |
| Runtime USB VID/PID and control interface | Distinguish shared personalities. |
| Boot USB VID/PID and descriptor | Evidence for target discrimination, not PID alone. |
| HID descriptor/report layout | Hash, report IDs/sizes, relevant usage/interface. |
| Firmware PID, model tag, catalog type | Explain the mapping and its source. |
| Starting firmware and beta status | How they were measured. |

## Official artifacts and protocol sources

Record product/updater/firmware URLs, retrieval dates, updater version and hashes, exact firmware file SHA-256/size, and relevant source revisions or function offsets. Explain provenance limits and licensing of any reused code. Keep binaries and raw captures outside the contribution.

## Protocol and image layout

Describe identity, framing, response correlation, command-specific payloads, address units, protected regions, erase/write geometry, checksums/readback, commit, entry/reset, and reconnect behavior. Include a concise command sequence with side effects and failure handling. Identify which facts are observed and which are inferred.

Explain source-version and hardware compatibility checks, authorization fields, and why an existing backend can be reused or a new one is required. Record the destination byte/page count and boundaries checked offline.

## Validation record

| Stage | Procedure, result, and limits |
| --- | --- |
| Parser/negative fixtures | Independently sourced observations and synthetic cases. |
| Full simulated update/recovery | Commands, bytes/pages, injected failures, expected stopping points. |
| Live identity/existing-data probes | Exact reports/limits, verified scope, any omitted regions. |
| Boot entry and return | Authorization scope, assumptions, observed identity/version, no-write bounds. |
| Official update capture | Observed transaction or reason unavailable. |
| Native installation | Exact authorized target/image, verification, reconnect, final job result. |
| Functional checks | Input, pairing, reconnect, relevant accessory behavior. |
| Interrupted-update recovery | Implemented route, tested states, and untested physical failure modes. |
| Software/package validation | Commands, environment, pass/fail/skip counts, CI link when available. |

## Recovery and unresolved work

Describe how an interrupted job is identified, which checks are non-mutating, which confirmed action changes the device, and the conditions that block it. Identify any externally assisted recovery requirement accurately. Do not claim rollback or a vendor fallback without evidence for this component.

List remaining questions, disabled operations, and the concrete next evidence needed. Link the implementation, tests, support matrix, and any related issues/PRs. Exclude serial numbers, personal paths, USB topology, and proprietary dumps from published evidence.
