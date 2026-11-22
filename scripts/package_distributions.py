"""Assemble native distribution files and app units for signed tufup publication."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile

from check_version import project_version

ROOT = Path(__file__).resolve().parent.parent
SYSTEM = {"linux": "linux", "darwin": "macos", "win32": "windows"}[sys.platform]
ARCH = {"AMD64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}[platform.machine()]
VERSION = str(project_version(ROOT))
SUFFIX = ".exe" if SYSTEM == "windows" else ""


def archive_update(product, application):
    output = ROOT / "out/update-inputs"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"eightbitdo-{product}-{SYSTEM}-{ARCH}.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(application, arcname="application")


def archive_directory(directory, destination, *, zip_format=False):
    if zip_format:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
            for path in directory.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(directory.parent))
    else:
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(directory, arcname=directory.name)


def prepare():
    subprocess.run([sys.executable, str(ROOT / "packaging/collect_licenses.py"), str(ROOT / "out/licenses")], check=True)
    metadata = json.loads(subprocess.check_output([
        "cargo", "metadata", "--locked", "--format-version", "1",
        "--manifest-path", str(ROOT / "src-tauri/Cargo.toml"),
    ], encoding="utf-8"))
    for package in metadata["packages"]:
        if package.get("source") is None:
            continue
        directory = Path(package["manifest_path"]).parent
        for notice in directory.iterdir():
            if notice.is_file() and notice.name.lower().startswith(("license", "copying", "notice")):
                target = ROOT / "out/licenses" / (package["name"] + "-" + package["version"])
                target.mkdir(exist_ok=True)
                shutil.copy2(notice, target / notice.name)
    for package in ("react", "react-dom", "@tauri-apps/api"):
        directory = ROOT / "node_modules" / package
        for notice in directory.iterdir():
            if notice.is_file() and notice.name.lower().startswith(("license", "copying", "notice")):
                target = ROOT / "out/licenses" / package.replace("/", "-")
                target.mkdir(exist_ok=True)
                shutil.copy2(notice, target / notice.name)
    for notice in (ROOT / "node_modules/.pnpm").glob("scheduler@*/node_modules/scheduler/LICENSE"):
        shutil.copy2(notice, ROOT / "out/licenses/React-scheduler-LICENSE")
    host = next(line.split(": ", 1)[1] for line in subprocess.check_output(["rustc", "-vV"], text=True).splitlines() if line.startswith("host: "))
    executable = ROOT / "out/binary" / ("eightbitdo" + SUFFIX)
    sidecar = ROOT / "src-tauri/binaries" / (f"eightbitdo-{host}" + SUFFIX)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, sidecar)
    directory = ROOT / "out" / f"eightbitdo-updater-{VERSION}-{SYSTEM}-{ARCH}"
    directory.mkdir(exist_ok=True)
    shutil.copy2(executable, directory / executable.name)
    shutil.copy2(ROOT / "packaging/BINARY_NOTICES.md", directory)
    shutil.copytree(ROOT / "out/licenses", directory / "licenses", dirs_exist_ok=True)
    release = ROOT / "out/release"
    release.mkdir(exist_ok=True)
    extension = ".zip" if SYSTEM == "windows" else ".tar.gz"
    archive_directory(directory, release / (directory.name + extension), zip_format=SYSTEM == "windows")
    archive_update("cli", executable)


def one(pattern):
    files = list((ROOT / "src-tauri/target/release").glob(pattern))
    if len(files) != 1:
        raise ValueError(f"Expected one native bundle for {pattern}, found {len(files)}")
    return files[0]


def desktop():
    release = ROOT / "out/release"
    name = f"8BitDo-Updater_{VERSION}_{SYSTEM}-{ARCH}"
    if SYSTEM == "linux":
        application = one("bundle/appimage/*.AppImage")
        shutil.copy2(application, release / (name + ".AppImage"))
    elif SYSTEM == "macos":
        application = one("bundle/macos/*.app")
        shutil.copy2(one("bundle/dmg/*.dmg"), release / (name + ".dmg"))
    else:
        application = ROOT / "out/portable" / name
        application.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "src-tauri/target/release/eightbitdo-updater.exe", application)
        shutil.copy2(ROOT / "out/binary/eightbitdo.exe", application)
        shutil.copytree(ROOT / "out/licenses", application / "licenses", dirs_exist_ok=True)
        shutil.copy2(one("bundle/nsis/*.exe"), release / (name + "-setup.exe"))
        archive_directory(application, release / (name + "-portable.zip"), zip_format=True)
    archive_update("desktop", application)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "desktop"])
    {"prepare": prepare, "desktop": desktop}[parser.parse_args().action]()
