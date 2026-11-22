"""Publish/refresh tufup metadata using online role keys supplied by CI."""

import argparse
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

from check_version import project_version
from tuf_repo import add_bundle, refresh, validate_signatures, open_repository

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = (("linux", "x86_64"), ("linux", "aarch64"), ("macos", "x86_64"),
             ("macos", "aarch64"), ("windows", "x86_64"))
REPOSITORIES = tuple(f"eightbitdo-{product}-{system}-{arch}"
                     for system, arch in PLATFORMS for product in ("cli", "desktop"))
KEY_FILES = frozenset({"root.pub", "targets.pub", "snapshot.pub", "timestamp.pub",
                       "targets", "snapshot", "timestamp"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "refresh"])
    parser.add_argument("--metadata-checkout", required=True, type=Path)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--release-assets", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--notes-file", type=Path)
    args = parser.parse_args()
    if args.action == "prepare" and not all((args.inputs, args.release_assets, args.tag, args.notes_file)):
        parser.error("prepare requires --inputs, --release-assets, --tag, and --notes-file")
    notes = args.notes_file.read_text(encoding="utf-8") if args.action == "prepare" else ""
    if args.action == "prepare" and not notes.strip():
        parser.error("release notes must not be empty")
    secret = json.loads(os.environ.pop("EIGHTBITDO_TUF_SIGNING_KEYS"))
    if set(secret) != KEY_FILES or not all(isinstance(value, str) for value in secret.values()):
        raise ValueError("Expected public role keys and three online private keys; never the offline root private key.")
    with tempfile.TemporaryDirectory(prefix="eightbitdo-online-signing-") as temporary:
        working = Path(temporary)
        keys = working / "keys"
        keys.mkdir(mode=0o700)
        for name, value in secret.items():
            path = keys / name
            path.write_text(value, encoding="utf-8")
            path.chmod(0o600)
        for name in REPOSITORIES:
            repository = working / name
            shutil.copytree(args.metadata_checkout / name / "metadata", repository / "metadata")
            if args.action == "prepare":
                source = working / (name + "-input")
                source.mkdir()
                with tarfile.open(args.inputs / (name + ".tar.gz")) as archive:
                    archive.extractall(source, filter="data")
                if {path.name for path in source.iterdir()} != {"application"}:
                    raise ValueError(f"Unexpected app-unit layout for {name}")
                target = add_bundle(repository, keys, name, source / "application",
                                    str(project_version(ROOT)), args.tag, notes=notes)
                args.release_assets.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, args.release_assets / target.name)
            else:
                refresh(repository, keys, name)
            with open_repository(repository, keys, name) as repo:
                validate_signatures(repo)
            public = args.metadata_checkout / name / "metadata"
            shutil.copytree(repository / "metadata", public, dirs_exist_ok=True)
            for path in public.iterdir():
                path.chmod(0o644)
    print(f"{args.action}: validated signed metadata for {len(REPOSITORIES)} app/platform repositories.")


if __name__ == "__main__":
    main()
