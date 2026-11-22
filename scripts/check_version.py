"""Check Python, Decky, and optional release-tag versions using PEP 440."""

import argparse
import ast
import json
from pathlib import Path
import re
import tomllib

from packaging.version import Version


def project_version(root: Path) -> Version:
    module = ast.parse((root / "eightbitdo/__init__.py").read_text())
    value = next(ast.literal_eval(node.value) for node in module.body if
                 isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
    version = Version(value)
    if Version(json.loads((root / "package.json").read_text())["version"]) != version:
        raise ValueError("Python and Decky versions differ")
    if (root / "src-tauri/tauri.conf.json").exists():
        desktop = json.loads((root / "src-tauri/tauri.conf.json").read_text())["version"]
        rust = tomllib.loads((root / "src-tauri/Cargo.toml").read_text())["package"]["version"]
        if Version(desktop) != version or Version(rust) != version:
            raise ValueError("Python, desktop, and Rust package versions differ")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    version = project_version(Path(__file__).resolve().parent.parent)
    if args.tag:
        if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", args.tag):
            parser.error("Use vX.Y.Z or vX.Y.Z-alpha.N, -beta.N, or -rc.N")
        if Version(args.tag[1:]) != version:
            parser.error("Release tag does not match the checked-out package version")
    print(version)


if __name__ == "__main__":
    main()
