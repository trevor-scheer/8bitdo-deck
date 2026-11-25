"""Build and smoke-test the allowlisted Decky installation archive."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


PACKAGE_NAME = "8bitdo-deck"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
REQUIRED_FILES = (
    "package.json", "plugin.json", "main.py", "eightbitdo/__init__.py", "dist/index.js",
    "scripts/install-udev-rules.sh", "eightbitdo/resources/__init__.py",
    "eightbitdo/resources/install-udev-rules.sh",
)
DECKY_API_MATERIALS = (
    "THIRD_PARTY_NOTICES.md", "third_party/licenses/decky-api-LGPL-2.1.txt", "third_party/licenses/tslib.txt",
    "third_party/decky-api/LICENSE", "third_party/decky-api/README.md", "third_party/decky-api/package.json",
    "third_party/decky-api/tsconfig.json", "third_party/decky-api/src/index.ts",
    "third_party/decky-api/src/types.ts", "third_party/decky-api/src/types.d.ts",
    "docs/rebuild-library.md", "src/index.tsx", "src/api.ts", "rollup.config.js",
    "src/shared/DevicePicture.tsx", "src/shared/device-artwork.ts", "src/shared/preparation.ts",
    "src/shared/theme.ts", "src/shared/types.ts",
    "src/decky/firmware.ts", "src/decky/useDeviceUpdates.ts",
    "tsconfig.json", "pnpm-lock.yaml", "scripts/package.py",
)


def package_files(root: Path) -> list[Path]:
    manifest = json.loads((root / "plugin.json").read_text())
    package = json.loads((root / "package.json").read_text())
    paths = {Path(name) for name in REQUIRED_FILES}
    paths.update(path.relative_to(root) for path in (root / "eightbitdo").rglob("*.py"))
    paths.update(path.relative_to(root) for path in (root / "eightbitdo/resources").glob("*.sh"))
    paths.update(path.relative_to(root) for path in (root / "docs").glob("*.md"))
    if "@decky/api" in package.get("dependencies", {}):
        paths.update(Path(name) for name in DECKY_API_MATERIALS)
    for name in ("LICENSE", "README.md"):
        if (root / name).is_file():
            paths.add(Path(name))
    for relative in paths:
        source = root / relative
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError(f"Missing or unsafe package file: {relative}")
    if manifest.get("name") != "8BitDo Deck" or package.get("name") != PACKAGE_NAME:
        raise ValueError("Package and plugin names do not match this project.")
    if not (root / "dist/index.js").stat().st_size:
        raise ValueError("The frontend bundle is empty. Run pnpm run build first.")
    return sorted(paths)


def smoke_test(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="8bitdo-package-check-") as temporary:
        temporary_path = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts or path.parts[0] != PACKAGE_NAME:
                    raise ValueError("Archive contains a path outside its package folder.")
            bundle.extractall(temporary_path)
        package_dir = temporary_path / PACKAGE_NAME
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["XDG_STATE_HOME"] = str(temporary_path / "state")
        script = """
import importlib.util
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("packaged_plugin", root / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
plugin = module.Plugin()
import eightbitdo
assert Path(eightbitdo.__file__).resolve().is_relative_to(root), "Backend came from outside the archive"
assert plugin.service is not None, "Plugin service was not initialized"
print("Packaged backend imports and initializes in isolation.")
"""
        subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(package_dir)],
            cwd=temporary_path, env=environment, check=True, timeout=30,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )


def build_package(root: Path, destination: Path) -> str:
    root = root.resolve()
    paths = package_files(root)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".8bitdo-package-", suffix=".zip", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for relative in paths:
                entry = zipfile.ZipInfo(f"{PACKAGE_NAME}/{relative.as_posix()}", FIXED_TIMESTAMP)
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(entry, (root / relative).read_bytes(), compresslevel=9)
        smoke_test(temporary)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.replace(temporary, destination)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Destination ZIP; defaults to out/8bitdo-deck.zip")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    destination = args.output or root / "out" / f"{PACKAGE_NAME}.zip"
    try:
        digest = build_package(root, destination)
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"Packaged backend smoke test failed:\n{error.stderr}\n")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Could not build package: {error}\n")
    print(f"Created {destination}\nSHA-256: {digest}\nIsolated backend smoke test passed.")


if __name__ == "__main__":
    main()
