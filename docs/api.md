# Public updater API v1

Use `eightbitdo.api.UpdaterAPI` when embedding the backend in Python. Use `eightbitdo api` when calling it from another language or application. Both use the same operation set and firmware policy as the desktop, CLI, and Decky frontends. No Decky installation or network daemon is required.

## Python interface

```python
from eightbitdo.api import API_VERSION, APIError, UpdaterAPI

updater = UpdaterAPI()
response = updater.request("info", api_version=API_VERSION)
if response["ok"]:
    print(response["result"])
else:
    print(response["error"]["code"], response["error"]["message"])

# call() returns the result directly and raises APIError on failure.
try:
    models = updater.call("get_models")
except APIError as error:
    print(error.code, str(error))
```

`UpdaterAPI(state_dir=PATH)` chooses a different persistent state directory. Omit it to share default state with the desktop, CLI, and Decky. Linux preserves the existing `eightbitdo-deck` directory; macOS and Windows use their per-user `8BitDo Updater` state directory. `service=...` supplies a service implementation for testing; frontend code should normally use the default service.

`request(operation, parameters=None, *, api_version=1)` returns a JSON-compatible envelope. `call(operation, **parameters)` is the convenience form for Python callers. A successful request returns:

```json
{"api_version": 1, "ok": true, "result": {}}
```

An unsuccessful request returns:

```json
{"api_version": 1, "ok": false, "error": {"code": "unknown_operation", "message": "..."}}
```

The `result` value depends on the operation and can be an object or array. An error message is display text, not a stable value to parse; use `error.code` for program decisions. The returned `api_version` identifies the backend's supported contract, including when it rejects the caller's requested version.

The error codes are:

| Code | Meaning |
| --- | --- |
| `unsupported_api_version` | The requested contract version is not supported. |
| `unknown_operation` | The operation is not part of the public API. |
| `invalid_parameters` | Parameters are missing, unexpected, or have the wrong type. |
| `confirmation_required` | A mutating operation lacks explicit boolean confirmation. |
| `unsupported_platform` | The default device service cannot run on this host OS. |
| `permission_denied` | A required operation was denied by host permissions. |
| `timeout` | An operation exceeded its response or reconnect deadline. |
| `io_error` | A device, file, or host I/O operation failed. |
| `operation_rejected` | The service rejected a value or operation for the current state. |
| `operation_failed` | The service reported an operational failure. |
| `internal_error` | Execution failed unexpectedly or returned a non-JSON result. |

Service methods can return a failure or raise one; that distinction affects whether the boundary reports `operation_failed` or a more specific exception code. Do not retry firmware mutations solely because an error appears transient.

## CLI interface

```sh
eightbitdo api info --api-version 1
eightbitdo api get_releases --params '{"model_id":"ultimate2-receiver"}'
eightbitdo api get_job --params '{"job_id":"JOB_ID"}'
```

Replace `JOB_ID` with a job ID returned by preparation. `--params` must contain a JSON object whose keys match the operation's parameters. Arguments containing JSON should be quoted as one shell argument. The `api` command prints the versioned envelope to standard output; ordinary commands such as `scan` and `job` retain their direct result JSON for compatibility.

Exit status is `0` for a successful operation, `1` for an API failure or a failed job result, and `2` for CLI usage errors. A queued or ready job is not a successful firmware installation; poll the job and inspect its state.

Both `setup-recovery-access --confirm` and `api setup_recovery_access` wait up to five minutes for administrator authorization. They report success only after recovery access is configured. Denial or incomplete setup returns `operation_failed`; the `api` command preserves the versioned error envelope. A timeout returns `timeout`, and Ctrl+C returns `operation_failed`. These failures exit with status `1`. Stopping the wait does not cancel the authorization process; dismiss any remaining prompt before retrying setup.

## Operations

Call `info` for the supported API/application versions, platform support, capabilities, and operation parameter descriptions. It does not create an update service or probe hardware. `--help` and `--version` likewise do not start a service or access devices.

| Operation | Parameters | Result and effects |
| --- | --- | --- |
| `info` | None | Application/API information and operation descriptions; no device probe. |
| `scan_devices` | None | `{devices, warnings}`; queries allowlisted runtime identities. |
| `probe_wireless` | None | Bounded identity results for reviewed radio routes on Linux; does not install firmware. |
| `get_models` | None | Model array, including available fwupd entries. Native models include advisory `download_only`: true when no installation route is enabled for that model on this host. The optional advisory `wireless_only` field marks a model whose enabled native installation route requires its receiver. False `download_only` or true `wireless_only` does not authorize a device or image; preparation still checks the exact target, host, and source version. |
| `get_releases` | `model_id` | `{releases: [...]}` for the selected component. |
| `prepare_update` | `model_id`, `release_id`; optional `device_id` | `{job_id}`; validates the selection and starts preparation. An omitted or empty device ID browses/downloads without identifying hardware. |
| `get_job` | `job_id` | Public job status, evidence, installation/recovery capabilities, and progress. |
| `install_update` | `job_id`, `confirmed` | Queues an eligible installation only when `confirmed` is exactly `true`. |
| `check_recovery` | `job_id` | Inspects an eligible interrupted job without flashing, resetting, or entering boot mode. |
| `recover_update` | `job_id`, `confirmed` | Queues the checked recovery action only when `confirmed` is exactly `true`. |
| `get_recovery_access` | None | Access configuration and any pending setup status. |
| `setup_recovery_access` | `confirmed` | Requests host administrator authorization for scoped recovery access only when `confirmed` is exactly `true`; does not write firmware. |
| `export_diagnostics` | None | Writes sanitized diagnostics and returns their local path. |
| `open_official_updater` | None | Asks the host browser integration to open the official website. |
| `check_app_update` | `channel` | Checks signed application metadata for `stable` or `alpha`; returns availability and any pending transaction. |
| `apply_app_update` | `channel`, `version`, `confirmed` | Downloads/verifies the exact reviewed application version and installs or stages it only with `confirmed: true`. |
| `recover_app_update` | `confirmed` | Rolls back or stages rollback of an interrupted application transaction only with `confirmed: true`. |

An operation name cannot select a private service method or invoke a worker directly. Unknown parameters, wrong types, and unsupported API versions are rejected before the operation runs. Installation and recovery confirmations are boolean parameters, not strings or truthy integers.

In the Python API and Decky adapter, `setup_recovery_access` returns immediately. Keep the same API instance alive and poll `get_recovery_access` while `pending` is true. Pending status and authorization errors belong to that instance; a separate process can inspect installed access configuration but cannot track another process's setup prompt. The CLI performs this polling before it exits.

## Wireless device and job results

A controller identified through its receiver has `backend: "wireless"`, model `ultimate2-controller`, and its own `paired-…` device ID. Pass that ID unchanged to `prepare_update`; do not substitute the receiver's `usb-…` ID or construct an ID from USB topology. The receiver remains a separate component with its own firmware releases. A status-only paired entry is not an installation authorization.

Model results can include optional advisory `download_only` and `wireless_only` booleans. A frontend should explain that a wireless-only model must be selected through its receiver. These fields describe model capabilities, not eligibility of a connected device, a particular release, or a host. Treat absent optional fields as unknown and rely on the prepared job's `can_install` result.

Eligible wireless jobs use the existing `prepare_update`, `get_job`, and explicitly confirmed `install_update` operations. On Linux, the reviewed route is stable controller 1.09 → exact official 1.10 through stable receiver 1.06, edition 1, with at least 30% battery and matching installed-code fingerprints. Preparation of an already-current 1.10 controller does not offer reinstallation. Normal direct-USB controller installation and Bluetooth installation are disabled. Catalog discovery of a future release does not create an authorization.

USB controller rescue is a distinct operation from normal wireless installation and requires separate confirmation before erase/reinstall. Display its checked recovery action and evidence; never interpret wireless installation confirmation as blanket recovery permission. See the [controller rescue contract](devices/ultimate2-controller-recovery.md) for protocol and hardware-validation limits.

## Platform capabilities

`platform_supported` means the host has discovery support, not that it can install firmware. The `capabilities` object contains:

| Capability | Linux | macOS / Windows |
| --- | --- | --- |
| `discovery`, `identity_query`, `firmware_download` | `true` | `true` |
| `native_write`, `recovery`, `fwupd`, `permission_setup`, `wireless_probe` | `true` | `false` |

The values describe implemented host operations. They do not confirm that a particular device was found, that its image is authorized, or that a firmware update was tested. Linux operations still depend on actual permissions, host services, and per-device/job checks. macOS/Windows native writes and recovery stay disabled until their boot/reconnect behavior is validated.

## Application update results

Application-update checks return `current_version`, `channel`, `platform`, `arch`, `available_version`, `release_notes`, `state`, `can_apply`, `can_recover`, `restart_required`, and `message`. Pass the exact reviewed `available_version` as `version` when applying. A changed available version is rejected instead of silently replacing the user's choice.

Check states include `unsupported`, `current`, `available`, `staged`, and `interrupted`. Installation or rollback can return `staged` while waiting for the desktop to close. Show the backend's message and a quit/reopen action; do not call a staged transaction complete. Offer rollback only when `can_recover` is true. Signature failures, expired metadata, and failed requests are errors, not an up-to-date result.

Apply requests perform download and verification before returning, so show indeterminate progress while waiting. Firmware jobs and application replacement exclude each other in the backend. Neither app-update confirmation nor successful installation authorizes a firmware write. See the [self-update contract](self-updates.md#frontend-api) for result states, supported layouts, and recovery.

## Frontend responsibilities

Display the selected component, source/destination versions, evidence level, and limitations before requesting installation. Preserve the distinction between preparation and firmware installation. When recovery inspection returns `reflash`, disclose that confirmation permits a complete erase/reinstall of the cached image; another recovery action can activate an already committed image by resetting the affected component. Controller USB rescue must retain its separate confirmation and target checks.

Poll `get_job` after submitting a background job. Active states are `queued`, `downloading`, `validating`, `installing`, and `verifying`; terminal presentation depends on `ready`, `blocked`, `failed`, or `complete`. A `ready` job can still fail revalidation when the worker starts. Only `complete` establishes the backend's post-update verification result.

Treat `can_install`, `can_check_recovery`, `recovery_ready`, and `recovery_action` as backend decisions, not fields a client may set. Manual model selection, a catalog release, or a successful request envelope cannot create a firmware authorization. The backend repeats target, image, source-version, access, and recovery checks at the relevant operation boundary.

Public job results omit cached-image paths and physical USB topology. Diagnostics exports contain a local file path so the user can retrieve the report; do not expose that path to unrelated remote clients. Device discovery can include host-specific IDs needed to select a device, so applications should avoid publishing raw results unnecessarily.

The v1 contract allows additive result fields and operation descriptions. Clients should ignore fields they do not use and reject unsupported API versions explicitly. Implementation classes such as `Service`, protocol transports, worker entrypoints, and internal job records are not the stable frontend API.
