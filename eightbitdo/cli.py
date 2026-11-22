import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .api import API_VERSION, UpdaterAPI


_SETUP_WAIT_SECONDS = 300


def _request(client, operation, parameters, *, api_version=API_VERSION):
    if operation != "setup_recovery_access":
        return client.request(operation, parameters, api_version=api_version)
    try:
        response = client.request(operation, parameters, api_version=api_version)
        deadline = time.monotonic() + _SETUP_WAIT_SECONDS
        while response["ok"] and response["result"].get("pending"):
            if time.monotonic() >= deadline:
                return _setup_error("timeout", "Timed out waiting for administrator authorization. The authorization prompt may still be open; dismiss it before retrying setup.")
            time.sleep(0.25)
            response = client.request("get_recovery_access", api_version=api_version)
        if response["ok"] and not response["result"].get("configured"):
            return _setup_error("operation_failed", response["result"].get("message") or "Recovery USB access was not configured.")
        return response
    except KeyboardInterrupt:
        return _setup_error("operation_failed", "Stopped waiting for administrator authorization. Setup may still be running; dismiss any authorization prompt before retrying setup.")


def _setup_error(code, message):
    return {"api_version": API_VERSION, "ok": False, "error": {"code": code, "message": message}}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--worker":
        if len(arguments) != 4:
            raise SystemExit("Expected --worker STATE_DIR JOB_ID ACTION.")
        from .worker import run
        run(Path(arguments[1]), arguments[2], arguments[3])
        return 0

    parser = argparse.ArgumentParser(description="8BitDo firmware updater for Linux")
    parser.add_argument("--version", action="version", version=f"8BitDo Updater {__version__}")
    parser.add_argument("--state-dir", type=Path, help="Directory for local firmware cache and jobs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="Show API version and operations without accessing hardware or state")
    sub.add_parser("scan", help="Identify connected devices and read firmware versions")
    sub.add_parser("probe-wireless", help="Read firmware identity over a reviewed radio path without starting an update")
    sub.add_parser("models", help="List model-specific update guidance")
    releases = sub.add_parser("releases", help="List official stable firmware releases")
    releases.add_argument("model_id")
    prepare = sub.add_parser("prepare", help="Download and inspect firmware without flashing")
    prepare.add_argument("model_id")
    prepare.add_argument("release_id")
    prepare.add_argument("--device-id", default="")
    job = sub.add_parser("job", help="Read a background job's status")
    job.add_argument("job_id")
    install = sub.add_parser("install", help="Install prepared firmware with a reviewed update authorization")
    install.add_argument("job_id")
    install.add_argument("--confirm", action="store_true", help="Confirm the target and version recorded in the job")
    check_recovery = sub.add_parser("check-recovery", help="Inspect recovery for a failed receiver update without resetting")
    check_recovery.add_argument("job_id")
    recover = sub.add_parser("recover", help="Recover a checked receiver update and verify its returning version")
    recover.add_argument("job_id")
    recover.add_argument("--confirm", action="store_true", help="Confirm the verified recovery action")
    sub.add_parser("recovery-access", help="Inspect Linux USB recovery permissions without changing them")
    setup = sub.add_parser("setup-recovery-access", help="Request scoped USB recovery access through administrator authorization")
    setup.add_argument("--confirm", action="store_true", help="Confirm opening the system authorization prompt")
    sub.add_parser("diagnostics", help="Export sanitized device information")
    sub.add_parser("official-updater", help="Open 8BitDo's official web updater")
    api = sub.add_parser("api", help="Call a versioned operation and print a JSON response envelope")
    api.add_argument("operation")
    api.add_argument("--params", default="{}", help="Operation parameters as a JSON object")
    api.add_argument("--api-version", type=int, default=API_VERSION)
    args = parser.parse_args(arguments)
    if args.command in {"install", "recover", "setup-recovery-access"} and not args.confirm:
        parser.error("Review the requested operation, then supply --confirm to continue.")

    client = UpdaterAPI(args.state_dir)
    if args.command == "api":
        try:
            parameters = json.loads(args.params)
            if not isinstance(parameters, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, RecursionError):
            response = {"api_version": API_VERSION, "ok": False,
                        "error": {"code": "invalid_parameters", "message": "Parameters must be a valid JSON object."}}
        else:
            response = _request(client, args.operation, parameters, api_version=args.api_version)
        print(json.dumps(response, indent=2))
        return int(not response["ok"] or _failed_job(response.get("result")))

    operations = {
        "info": ("info", {}),
        "scan": ("scan_devices", {}),
        "probe-wireless": ("probe_wireless", {}),
        "models": ("get_models", {}),
        "recovery-access": ("get_recovery_access", {}),
        "setup-recovery-access": ("setup_recovery_access", {"confirmed": True}),
        "diagnostics": ("export_diagnostics", {}),
        "official-updater": ("open_official_updater", {}),
    }
    if args.command == "releases":
        operation, parameters = "get_releases", {"model_id": args.model_id}
    elif args.command == "prepare":
        operation, parameters = "prepare_update", {"device_id": args.device_id, "model_id": args.model_id, "release_id": args.release_id}
    elif args.command in {"job", "install", "check-recovery", "recover"}:
        operation = {"job": "get_job", "install": "install_update", "check-recovery": "check_recovery", "recover": "recover_update"}[args.command]
        parameters = {"job_id": args.job_id}
        if args.command in {"install", "recover"}:
            parameters["confirmed"] = True
    else:
        operation, parameters = operations[args.command]
    response = _request(client, operation, parameters)
    if not response["ok"]:
        print(json.dumps({"error": response["error"]["message"]}), file=sys.stderr)
        return 1
    print(json.dumps(response["result"], indent=2))
    return int(_failed_job(response["result"]))


def _failed_job(result) -> bool:
    return isinstance(result, dict) and result.get("state") == "failed"


if __name__ == "__main__":
    raise SystemExit(main())
