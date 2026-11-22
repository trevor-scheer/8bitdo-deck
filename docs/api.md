# Public updater API v1

Use `eightbitdo.api.UpdaterAPI` when embedding the backend in Python. Use `eightbitdo api` when calling it from another language or application. Both use the same operation set and firmware policy as the CLI and Decky frontend. No Decky installation or network daemon is required.

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

`UpdaterAPI(state_dir=PATH)` chooses a different persistent state directory. Omit it to share the existing `eightbitdo-deck` state with the CLI and Decky. `service=...` supplies a service implementation for testing; frontend code should normally use the default service.

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

## Operations

Call `info` for the supported API/application versions, platform support, and operation parameter descriptions. It does not create an update service or probe hardware. `--help` and `--version` likewise do not start a service or access devices.

| Operation | Parameters | Result and effects |
| --- | --- | --- |
| `info` | None | Application/API information and operation descriptions; no device probe. |
| `scan_devices` | None | `{devices, warnings}`; queries allowlisted runtime identities. |
| `probe_wireless` | None | Bounded identity results for reviewed radio routes; does not install firmware. |
| `get_models` | None | Model array, including available fwupd entries. |
| `get_releases` | `model_id` | Release array for the selected component. |
| `prepare_update` | `model_id`, `release_id`; optional `device_id` | `{job_id}`; validates the selection and starts preparation. An omitted or empty device ID browses/downloads without identifying hardware. |
| `get_job` | `job_id` | Public job status, evidence, installation/recovery capabilities, and progress. |
| `install_update` | `job_id`, `confirmed` | Queues an eligible installation only when `confirmed` is exactly `true`. |
| `check_recovery` | `job_id` | Inspects an eligible interrupted job without flashing, resetting, or entering boot mode. |
| `recover_update` | `job_id`, `confirmed` | Queues the checked recovery action only when `confirmed` is exactly `true`. |
| `get_recovery_access` | None | Access configuration and any pending setup status. |
| `setup_recovery_access` | `confirmed` | Requests host administrator authorization for scoped recovery access only when `confirmed` is exactly `true`; does not write firmware. |
| `export_diagnostics` | None | Writes sanitized diagnostics and returns their local path. |
| `open_official_updater` | None | Asks the host browser integration to open the official website. |

An operation name cannot select a private service method or invoke a worker directly. Unknown parameters, wrong types, and unsupported API versions are rejected before the operation runs. Installation and recovery confirmations are boolean parameters, not strings or truthy integers.

## Frontend responsibilities

Display the selected component, source/destination versions, evidence level, and limitations before requesting installation. Preserve the distinction between preparation and firmware installation. When recovery inspection returns `reflash`, disclose that confirmation permits a complete erase/reinstall of the cached image; another recovery action can activate an already committed image by resetting the receiver.

Poll `get_job` after submitting a background job. Active states are `queued`, `downloading`, `validating`, `installing`, and `verifying`; terminal presentation depends on `ready`, `blocked`, `failed`, or `complete`. A `ready` job can still fail revalidation when the worker starts. Only `complete` establishes the backend's post-update verification result.

Treat `can_install`, `can_check_recovery`, `recovery_ready`, and `recovery_action` as backend decisions, not fields a client may set. Manual model selection, a catalog release, or a successful request envelope cannot create a firmware authorization. The backend repeats target, image, source-version, access, and recovery checks at the relevant operation boundary.

Public job results omit cached-image paths and physical USB topology. Diagnostics exports contain a local file path so the user can retrieve the report; do not expose that path to unrelated remote clients. Device discovery can include host-specific IDs needed to select a device, so applications should avoid publishing raw results unnecessarily.

The v1 contract allows additive result fields and operation descriptions. Clients should ignore fields they do not use and reject unsupported API versions explicitly. Implementation classes such as `Service`, protocol transports, worker entrypoints, and internal job records are not the stable frontend API.
