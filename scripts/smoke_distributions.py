"""Exercise installed distributions outside the checkout, without device access."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import venv


def check(command, root, environment):
    state = root / "state"

    def run(*args, expected=0):
        result = subprocess.run([*command, *args], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == expected, (args, result.stdout, result.stderr)
        return result.stdout

    assert "8BitDo Updater" in run("--version")
    assert "usage:" in run("--help")
    info = json.loads(run("--state-dir", str(state), "info"))
    assert info["api_version"] == 1 and info["platform_supported"] is True
    assert json.loads(run("api", "info"))["result"] == info
    mismatch = json.loads(run("api", "info", "--api-version", "999", expected=1))
    assert mismatch["error"]["code"] == "unsupported_api_version"
    denied = json.loads(run("--state-dir", str(state), "api", "install_update",
                            "--params", '{"job_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', expected=1))
    assert denied["error"]["code"] == "confirmation_required"
    assert not state.exists(), "Informational commands initialized mutable service state"

    # Unknown model fails before catalog access, hardware discovery, or writes.
    job_id = "a" * 32
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    path = jobs / (job_id + ".json")
    path.write_text(json.dumps(dict(id=job_id, state="queued", worker_action="prepare",
                                    model_id="invalid-smoke-model", device_id="", release_id="missing",
                                    created_at=time.time(), can_install=False, progress=0)))
    if len(command) == 1:
        run("--worker", str(state), job_id, "prepare")
    else:
        subprocess.run([command[0], "-I", "-m", "eightbitdo.worker", str(state), job_id, "prepare"],
                       cwd=root, env=environment, check=True, timeout=45)
    job = json.loads(path.read_text())
    assert job["state"] == "failed" and job["worker_pid"] > 0
    assert "known model" in job["message"] and not job["can_install"]

    # Public prepare with no device and a nonexistent release cannot flash or
    # download firmware. Its worker can fetch only bounded catalog metadata.
    prepared = json.loads(run("--state-dir", str(state), "prepare", "ultimate2-receiver",
                              "distribution-smoke-no-such-release"))
    background_path = jobs / (prepared["job_id"] + ".json")
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        background = json.loads(background_path.read_text())
        if background["state"] == "failed":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("Background worker did not finish after its launcher exited")
    assert background.get("worker_pid", 0) > 0 and background.get("worker_start")
    assert not background["can_install"] and background["device_id"] == ""
    assert "No module named" not in background["message"]
    print("Standalone API, confirmation gates, worker dispatch, and parent-exit lifetime passed.")
    print("No device was opened or firmware written; the lifecycle check requested catalog metadata only.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--binary", type=Path)
    group.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="eightbitdo-distribution-") as temporary:
        root = Path(temporary)
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["XDG_STATE_HOME"] = str(root / "default-state")
        if args.binary:
            command = [str(args.binary.resolve())]
            environment["PATH"] = str(root / "no-external-python")
        else:
            venv.EnvBuilder(with_pip=True).create(root / "venv")
            python = str(root / "venv/bin/python")
            subprocess.run([python, "-I", "-m", "pip", "install", "--no-index", "--no-deps",
                            str(args.wheel.resolve())], cwd=root, env=environment, check=True, timeout=60)
            subprocess.run([python, "-I", "-c", "from importlib.resources import files; "
                            "assert files('eightbitdo').joinpath('resources/install-udev-rules.sh').is_file(); "
                            "from eightbitdo.api import UpdaterAPI"], cwd=root, env=environment, check=True)
            command = [python, "-I", "-m", "eightbitdo"]
        check(command, root, environment)


if __name__ == "__main__":
    main()
