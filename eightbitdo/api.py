"""Versioned frontend boundary shared by the CLI and Decky adapter.

``request`` returns JSON-compatible API v1 envelopes. ``call`` returns the result
or raises APIError. Neither construction nor ``info`` opens hardware or creates
state. Service is an implementation detail; frontends cannot invoke its workers.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from . import __version__


API_VERSION = 1


class ErrorDetail(TypedDict):
    code: str
    message: str


class SuccessResponse(TypedDict):
    api_version: int
    ok: Literal[True]
    result: Any


class ErrorResponse(TypedDict):
    api_version: int
    ok: Literal[False]
    error: ErrorDetail


Response = SuccessResponse | ErrorResponse


class APIError(RuntimeError):
    """An API failure with a stable machine-readable code and display message."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class _Operation:
    parameters: tuple[tuple[str, type], ...] = ()
    optional: frozenset[str] = frozenset()
    confirmation: bool = False


_OPERATIONS = {
    "info": _Operation(),
    "scan_devices": _Operation(),
    "probe_wireless": _Operation(),
    "get_models": _Operation(),
    "get_releases": _Operation((("model_id", str),)),
    "prepare_update": _Operation((("device_id", str), ("model_id", str), ("release_id", str)), frozenset({"device_id"})),
    "get_job": _Operation((("job_id", str),)),
    "install_update": _Operation((("job_id", str), ("confirmed", bool)), confirmation=True),
    "install_updates": _Operation((("job_ids", list), ("confirmed", bool)), confirmation=True),
    "get_update_queue": _Operation(),
    "check_recovery": _Operation((("job_id", str),)),
    "recover_update": _Operation((("job_id", str), ("confirmed", bool)), confirmation=True),
    "export_diagnostics": _Operation(),
    "open_official_updater": _Operation(),
    "get_recovery_access": _Operation(),
    "setup_recovery_access": _Operation((("confirmed", bool),), confirmation=True),
    "check_app_update": _Operation((("channel", str),)),
    "apply_app_update": _Operation((("channel", str), ("version", str), ("confirmed", bool)), confirmation=True),
    "recover_app_update": _Operation((("confirmed", bool),), confirmation=True),
}


def _error(code: str, message: str) -> ErrorResponse:
    return {"api_version": API_VERSION, "ok": False, "error": {"code": code, "message": message}}


class UpdaterAPI:
    """Public local API. Supply a Service-compatible object for integration tests."""

    def __init__(self, state_dir: Path | None = None, *, service=None):
        self.state_dir = state_dir
        self._service = service
        self._service_lock = threading.Lock()

    @property
    def service(self):
        if self._service is None:
            if not (sys.platform.startswith("linux") or sys.platform in {"darwin", "win32"}):
                raise APIError("unsupported_platform", "Device operations require Linux, macOS, or Windows.")
            with self._service_lock:
                if self._service is None:
                    from .service import Service
                    self._service = Service(self.state_dir)
        return self._service

    def call(self, operation: str, **parameters):
        """Call an API v1 operation, returning its result or raising APIError."""
        response = self.request(operation, parameters)
        if not response["ok"]:
            raise APIError(response["error"]["code"], response["error"]["message"])
        return response["result"]

    def request(self, operation: str, parameters: dict | None = None, *, api_version: int = API_VERSION) -> Response:
        """Validate a versioned request before initializing the service or doing I/O."""
        if type(api_version) is not int or api_version != API_VERSION:
            return _error("unsupported_api_version", f"Supported API version: {API_VERSION}.")
        if not isinstance(operation, str) or operation not in _OPERATIONS:
            return _error("unknown_operation", "Unknown public API operation.")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            return _error("invalid_parameters", "Operation parameters must be a JSON object.")
        definition = _OPERATIONS[operation]
        expected = dict(definition.parameters)
        if set(parameters) - expected.keys():
            return _error("invalid_parameters", "Unexpected operation parameter.")
        for name, kind in definition.parameters:
            if name not in parameters:
                if name == "confirmed" and definition.confirmation:
                    return _error("confirmation_required", "Review the operation and explicitly confirm it first.")
                if name not in definition.optional:
                    return _error("invalid_parameters", f"Missing required parameter: {name}.")
            elif type(parameters[name]) is not kind:
                return _error("invalid_parameters", f"Parameter {name} must be {kind.__name__}.")
        if definition.confirmation and parameters.get("confirmed") is not True:
            return _error("confirmation_required", "Review the operation and explicitly confirm it first.")
        arguments = dict(parameters)
        if operation == "prepare_update":
            arguments.setdefault("device_id", "")
        if operation in {"install_update", "install_updates", "setup_recovery_access"}:
            arguments.pop("confirmed")
        try:
            if operation == "info":
                from .platform import platform_capabilities
                capabilities = platform_capabilities()
                result = {
                    "api_version": API_VERSION,
                    "app_version": __version__,
                    "platform": sys.platform,
                    "platform_supported": capabilities["discovery"],
                    "capabilities": capabilities,
                    "operations": {name: {
                        "parameters": {key: kind.__name__ for key, kind in spec.parameters},
                        "required": [key for key, _ in spec.parameters if key not in spec.optional],
                    } for name, spec in _OPERATIONS.items()},
                }
            else:
                result = getattr(self.service, operation)(**arguments)
            if isinstance(result, dict) and result.get("error"):
                return _error("operation_failed", str(result["error"]))
            # Reject non-JSON service regressions before they leak into a frontend.
            try:
                json.dumps(result, allow_nan=False)
            except (TypeError, ValueError):
                raise APIError("internal_error", "The backend returned a result that is not JSON-compatible.") from None
            return {"api_version": API_VERSION, "ok": True, "result": result}
        except APIError as error:
            return _error(error.code, str(error))
        except PermissionError as error:
            return _error("permission_denied", str(error))
        except TimeoutError as error:
            return _error("timeout", str(error))
        except OSError as error:
            return _error("io_error", str(error))
        except ValueError as error:
            return _error("operation_rejected", str(error))
        except RuntimeError as error:
            return _error("operation_failed", str(error))
        except Exception:
            return _error("internal_error", "The backend returned an invalid result or could not execute the operation.")
