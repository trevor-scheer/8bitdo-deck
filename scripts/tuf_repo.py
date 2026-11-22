#!/usr/bin/env python3
"""Build and refresh signed tufup repositories without exposing signing keys."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from packaging.version import Version
from tuf.api.metadata import Metadata
from tufup.repo import Keys, Repository, Roles

EXPIRATION_DAYS = dict(root=365, targets=30, snapshot=7, timestamp=2)
APP_NAME = re.compile(r'eightbitdo-(cli|desktop)-(linux|macos|windows)-(x86_64|aarch64)\Z')


@contextmanager
def open_repository(path, keys, app_name, *, root=None):
    path, keys = Path(path).resolve(), Path(keys).resolve()
    if not APP_NAME.fullmatch(app_name):
        raise ValueError('Invalid application/platform/architecture repository name.')
    for directory in (path, path / 'metadata', path / 'targets', keys):
        directory.mkdir(parents=True, exist_ok=True)
    if root and not (path / 'metadata/root.json').exists():
        shutil.copyfile(root, path / 'metadata/root.json')
        trusted = Metadata.from_file(str(root))
        shutil.copyfile(root, path / 'metadata' / f'{trusted.signed.version}.root.json')
    previous = Path.cwd()
    os.chdir(path)
    try:
        repo = Repository(app_name=app_name, repo_dir=path, keys_dir=keys, expiration_days=EXPIRATION_DAYS)
        repo.keys = Keys(keys)
        repo.roles = Roles(repo.metadata_dir)
        repo.roles.initialize(repo.keys, EXPIRATION_DAYS)
        yield repo
    finally:
        os.chdir(previous)


def validate_signatures(repo):
    root = Metadata.from_file(str(repo.metadata_dir / 'root.json'))
    for name in ('root', 'targets', 'snapshot', 'timestamp'):
        root.verify_delegate(name, Metadata.from_file(str(repo.metadata_dir / f'{name}.json')))


def initialize(path, keys, app_name, *, root=None):
    keys = Path(keys).resolve()
    keys.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not any(keys.iterdir()):
        previous_umask = os.umask(0o077)
        try:
            Keys(keys).create()
        finally:
            os.umask(previous_umask)
    if not all((keys / (name + '.pub')).is_file() for name in EXPIRATION_DAYS):
        raise ValueError('Incomplete public key set. Refusing to generate replacement keys.')
    with open_repository(path, keys, app_name, root=root) as repo:
        repo.publish_changes(private_key_dirs=[keys])
        validate_signatures(repo)


def add_bundle(path, keys, app_name, source, version, release_tag, notes=''):
    if not (Path(path) / 'metadata/root.json').is_file():
        raise ValueError('Initialize the repository from its trusted root before adding a release.')
    version = str(Version(version))
    if not re.fullmatch(r'v[0-9][A-Za-z0-9.+-]*', release_tag):
        raise ValueError('Invalid immutable release tag.')
    source = Path(source).resolve()
    product, system, arch = APP_NAME.fullmatch(app_name).groups()
    with open_repository(path, keys, app_name) as repo:
        latest = repo.roles.get_latest_archive()
        if latest and Version(version) <= latest.version:
            raise ValueError('A release must be newer than every published version in this repository.')
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            destination = bundle / 'application'
            if source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
            elif source.is_file() and not source.is_symlink():
                shutil.copy2(source, destination)
            else:
                raise ValueError('Application payload must be an existing file or directory.')
            manifest = dict(schema=1, product=product, platform=system, arch=arch, version=version)
            (bundle / 'update-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            repo.add_bundle(bundle, new_version=version, skip_patch=True,
                            custom_metadata={**manifest, 'release_tag': release_tag, 'release_notes': notes})
        repo.publish_changes(private_key_dirs=[keys])
        validate_signatures(repo)
        return repo.targets_dir / f'{app_name}-{version}.tar.gz'


def refresh(path, keys, app_name):
    if not (Path(path) / 'metadata/root.json').is_file():
        raise ValueError('Cannot refresh a repository without its existing metadata.')
    with open_repository(path, keys, app_name) as repo:
        for name in ('targets', 'snapshot', 'timestamp'):
            repo.refresh_expiration_date(name)
        repo.publish_changes(private_key_dirs=[keys])
        validate_signatures(repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'add', 'refresh'])
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--keys', type=Path, required=True)
    parser.add_argument('--app-name', required=True)
    parser.add_argument('--root', type=Path, help='Existing trusted root for a new repository')
    parser.add_argument('--bundle', type=Path, help='Standalone executable, AppImage, .app, or portable Windows app directory')
    parser.add_argument('--version')
    parser.add_argument('--release-tag')
    parser.add_argument('--notes-file', type=Path)
    args = parser.parse_args()
    if not APP_NAME.fullmatch(args.app_name):
        parser.error('Invalid --app-name')
    if args.action == 'init':
        initialize(args.repository, args.keys, args.app_name, root=args.root)
    elif args.action == 'refresh':
        refresh(args.repository, args.keys, args.app_name)
    else:
        if not all((args.bundle, args.version, args.release_tag)):
            parser.error('add requires --bundle, --version, and --release-tag')
        artifact = add_bundle(args.repository, args.keys, args.app_name, args.bundle, args.version,
                              args.release_tag, args.notes_file.read_text() if args.notes_file else '')
        print(artifact)


if __name__ == '__main__':
    main()
