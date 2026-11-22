"""Run the packaged desktop's fixed, read-only sidecar smoke mode."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="eightbitdo-desktop-smoke-") as temporary:
        directory = Path(temporary)
        environment["XDG_STATE_HOME"] = str(directory / "state")
        if sys.platform == "linux":
            binary = next((ROOT / "out/release").glob("*.AppImage"))
            subprocess.run([str(binary), "--appimage-extract"], cwd=directory, env=environment,
                           stdout=subprocess.DEVNULL, check=True, timeout=60)
            command = ["xvfb-run", "-a", str(directory / "squashfs-root/AppRun"), "--smoke-test"]
        elif sys.platform == "darwin":
            app = next((ROOT / "src-tauri/target/release/bundle/macos").glob("*.app"))
            command = [str(app / "Contents/MacOS/eightbitdo-updater"), "--smoke-test"]
        else:
            binary = next((ROOT / "out/portable").glob("*/eightbitdo-updater.exe"))
            command = [str(binary), "--smoke-test"]
        result = subprocess.run(command, cwd=directory, env=environment, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise RuntimeError(f"Desktop smoke check failed: {result.stdout}\n{result.stderr}")
        # GUI-subsystem Windows binaries need not inherit a console for stdout.
        if sys.platform != "win32" and "Desktop sidecar API smoke test passed." not in result.stdout:
            raise RuntimeError(f"Desktop smoke check did not reach its API check: {result.stdout}\n{result.stderr}")
        print("Packaged desktop launched its bundled backend and validated API/application versions.")


if __name__ == "__main__":
    main()
