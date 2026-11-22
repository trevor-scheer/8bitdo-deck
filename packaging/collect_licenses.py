"""Collect component notices from the environment used to build the executable."""

import importlib.metadata
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--manifest', type=Path,
                        default=Path(__file__).resolve().parent.parent / 'out/binary-libraries.json')
    args = parser.parse_args()
    destination = args.destination
    destination.mkdir(parents=True, exist_ok=True)
    python_license = next(path for path in (
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
    ) if path.is_file())
    shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
    distribution = importlib.metadata.distribution("pyinstaller")
    license_file = next(file for file in distribution.files if str(file).endswith("/licenses/COPYING.txt"))
    shutil.copyfile(distribution.locate_file(license_file), destination / "PyInstaller-COPYING.txt")
    for package in importlib.metadata.distributions():
        name = package.metadata.get("Name", "unknown")
        for file in package.files or ():
            if not Path(file).name.lower().startswith(("license", "copying", "notice")):
                continue
            source = package.locate_file(file)
            if source.is_file():
                target = destination / (name + "-" + package.version) / str(file).replace("/", "_").replace("\\", "_")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    for _, source, _ in json.loads(args.manifest.read_text()):
        path = Path(source)
        if sys.platform != "linux":
            # Native extensions ship their licenses in their wheel metadata.
            # Homebrew's Python dependencies retain notices in their keg root.
            for parent in path.parents:
                if (parent / "INSTALL_RECEIPT.json").is_file():
                    package_name = parent.name if parent.parent.name == "opt" else parent.parent.name
                    for notice in parent.iterdir():
                        if notice.is_file() and notice.name.lower().startswith(("license", "copying", "notice")):
                            shutil.copyfile(notice, destination / (package_name + "-" + notice.name))
                    break
            continue
        if not source.startswith(("/usr/lib/", "/lib/")):
            prefixes = [sys.base_prefix, sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]
            if not any(path.resolve().is_relative_to(Path(prefix).resolve()) for prefix in prefixes):
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
