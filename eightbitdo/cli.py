import argparse
import json
import sys
from pathlib import Path

from .service import Service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="8BitDo firmware tools for Steam Deck")
    parser.add_argument("--state-dir", type=Path, help="Directory for local firmware cache and jobs")
    sub = parser.add_subparsers(dest="command", required=True)
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
    sub.add_parser("diagnostics", help="Export sanitized device information")
    sub.add_parser("official-updater", help="Open 8BitDo's web updater in Chrome")
    args = parser.parse_args(argv)
    service = Service(args.state_dir)
    try:
        match args.command:
            case "scan":
                result = service.scan_devices()
            case "probe-wireless":
                result = service.probe_wireless()
            case "models":
                result = service.get_models()
            case "releases":
                result = service.get_releases(args.model_id)
            case "prepare":
                result = service.prepare_update(args.device_id, args.model_id, args.release_id)
            case "job":
                result = service.get_job(args.job_id)
            case "install":
                if not args.confirm:
                    parser.error("Review the job's target/version, then supply --confirm to install.")
                result = service.install_update(args.job_id)
            case "check-recovery":
                result = service.check_recovery(args.job_id)
            case "recover":
                if not args.confirm:
                    parser.error("Review check-recovery first, then supply --confirm to recover.")
                result = service.recover_update(args.job_id, confirmed=True)
            case "diagnostics":
                result = service.export_diagnostics()
            case "official-updater":
                result = service.open_official_updater()
        print(json.dumps(result, indent=2))
        return int(isinstance(result, dict) and ("error" in result or result.get("state") == "failed"))
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
