# Validate a new device path

Use the contributor's actual host and connection. Do not assume the maintainer's Steam Deck, SSH aliases, permissions, or attached devices exist. Keep a record of every hardware operation and its result in the device evidence note.

## Offline validation

1. Check identity decoding against independently obtained observations and synthetic negative cases. A fake transport that mirrors the implementation is not independent protocol evidence.
2. Parse the official image without touching hardware. Check its provenance receipt, full-file hash, container fields, protected regions, address translations, erase rounding, and planned byte/page counts. Compare the plan with the official implementation or capture.
3. Exercise the complete transaction and recovery using a simulated transport, including failures before erase, during transfer, before/after commit, and on reconnect. Ensure the worker cannot bypass the compiled policy or a required confirmation. Check stale/foreign HID nodes, lock contention, failed persistence, missing permissions, and suspend-inhibitor launch failure where the new path changes those behaviors.
4. Verify discovery/catalog-only support stays non-installable. Verify any experimental route displays its actual evidence and limits before confirmation. Existing supported families must retain their behavior.

## Identity and existing-data checks

Prepare an allowlist of the exact evidence-backed reports, allowed targets/modes, maximum exchanges, deadlines, and expected responses. Use a transport wrapper or equivalent bounds to prevent the test from sending another command. Lock the target and retain its original physical port privately.

Send only the documented identity/status queries initially. Compare the result with the physical model, catalog mapping, and starting version. Stop on an unexpected identity or reply; do not widen the allowlist to keep probing.

If readback/checksum commands are proven non-mutating for this exact route, test them against bounded existing data. Check any length quantization and tail handling. Do not interpret reading one page as fingerprinting the entire application or bootloader. Do not use a receiver's direct-USB checksum evidence to authorize its radio-forwarded command.

## Boot round trip

Boot entry and reset are state-changing tests even when the host sends no image writes. Before running them:

1. Trace the exact entry and reset handlers, including boot selection, migration, pending-image activation, and any persistent effects. Explain the assumptions and residual uncertainty. A known boot USB PID alone is insufficient.
2. Verify boot-interface access and a credible return/recovery route for this model. A vendor web link, generic PAIR-button instruction, or another model's recovery behavior is not evidence. If safe return cannot be established within the owner's risk tolerance, leave entry blocked and continue offline work.
3. Present the concrete bounded test: target, source version, exact allowed commands, expected USB transition, stop conditions, and how to return. Proceed only within the owner's existing authorization; request missing authorization before the state change. Approval for identity reads or a low-risk round trip does not authorize erase/write/commit.
4. Keep USB/power stable and prevent suspend. Acquire temporary keep-awake settings only when needed and restore settings owned by this task; do not stop another task's lease.
5. Run one bounded entry, open the same physical port with fd-based descriptor/identity validation, inspect only proven-safe state, and perform the reviewed return. Keep polling through reset where required. Treat disconnect-before-ACK as provisional only when the specific implementation supports it; require the same target/version after reconnect.
6. Record the outcome and any existing-data checks before/after. Stop after an unexpected result. Reassess evidence rather than retrying state changes blindly.

A passed boot round trip demonstrates only the exercised path. It does not establish a successful flash, pending-image copy, interrupted-write recovery, or power-loss safety.

## First native installation

Before requesting permission to install, finish the code, tests, image validation, recovery implementation or concrete recovery limitation, and narrow experimental authorization. The owner should review a specific target/source/destination and remaining risk, not approve an unfinished plan. A first experiment does not require a fictional prior hardware qualification.

When the owner authorizes the exact update, use the production service/UI path with the reviewed image, locks, durable job, access checks, and suspend inhibition. Do not bypass the gate with an ad hoc flash script. If the owner will perform installation personally, prepare it and hand off at the confirmation screen; do not confirm for them.

After installation, record:

- Verification coverage before commit and any protocol-required post-commit checks.
- Same-port, verified firmware target, stable destination version, and final job result. A successful command acknowledgment alone is not completion.
- Normal input/pairing/reconnect behavior and any affected dock/receiver function. Record the controller and receiver versions separately.
- The exact evidence-backed scope eligible for `FlashQualification`; retain untested recovery and power-loss limits. A successful update does not qualify every future version or sibling product.

On failure, preserve the job, cached image, last stage, and sanitized error evidence. Use the implemented recovery check only if its commands are established safe for that state. Recovery that resets, activates pending firmware, or erases/reinstalls needs its own applicable authorization and explicit confirmation; do not silently retry the original update.

Test interruption handling with simulated/recorded responses first. Physical power-loss or forced-disconnect testing requires a recoverable test device, a deliberate procedure, and authorization for that risk. It is not a default prerequisite for a discovery-only contribution.
