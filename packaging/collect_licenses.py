"""Collect actual runtime license notices from the Linux build environment."""

import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


def main():
    destination = Path(sys.argv[1])
    destination.mkdir(parents=True, exist_ok=True)
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
    distribution = importlib.metadata.distribution("pyinstaller")
    license_file = next(file for file in distribution.files if str(file).endswith("/licenses/COPYING.txt"))
    shutil.copyfile(distribution.locate_file(license_file), destination / "PyInstaller-COPYING.txt")
    manifest = Path(__file__).resolve().parent.parent / "out/binary-libraries.json"
    for _, source, _ in json.loads(manifest.read_text()):
        path = Path(source)
        if not source.startswith(("/usr/lib/", "/lib/")):
            # CPython and its extension modules belong to the Python installation.
            if not path.resolve().is_relative_to(Path(sys.base_prefix).resolve()):
                raise ValueError(f"Unrecognized bundled library provenance: {source}")
            continue
        result = subprocess.run(["dpkg-query", "-S", source], capture_output=True, text=True)
        if result.returncode:
            # Accommodate merged /usr systems, retaining dpkg's registered spelling.
            alternate = source.removeprefix("/usr") if source.startswith("/usr/lib/") else "/usr" + source
            result = subprocess.run(["dpkg-query", "-S", alternate], capture_output=True, text=True, check=True)
        package = result.stdout.split(": ", 1)[0].split(":", 1)[0]
        shutil.copyfile(Path("/usr/share/doc") / package / "copyright", destination / (package + "-copyright"))
    print(f"Collected {len(list(destination.iterdir()))} runtime license notices.")


if __name__ == "__main__":
    main()
