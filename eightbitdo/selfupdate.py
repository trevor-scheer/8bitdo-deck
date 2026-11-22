"""Signed application updates. Firmware updates remain a separate operation."""
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
import json
import os
from pathlib import Path
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from . import __version__
from .jobs import JobStore, default_state_dir
from .runtime import external_environment, frozen_application


@dataclass(frozen=True)
class Installation:
    path: Path
    product: str
    platform: str
    arch: str
    desktop_pid: int | None = None

    @property
    def app_name(self):
        return f'eightbitdo-{self.product}-{self.platform}-{self.arch}'


def installation():
    if not frozen_application():
        return None
    system = {'Linux': 'linux', 'Darwin': 'macos', 'Windows': 'windows'}.get(platform.system())
    arch = {'AMD64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'aarch64', 'aarch64': 'aarch64'}.get(platform.machine())
    if not system or not arch:
        return None
    if os.environ.get('EIGHTBITDO_FRONTEND') == 'desktop':
        raw = os.environ.get('EIGHTBITDO_DESKTOP_APP_PATH')
        pid = os.environ.get('EIGHTBITDO_DESKTOP_PID', '')
        if not raw or not pid.isdecimal() or int(pid) <= 0:
            return None
        path = Path(raw).absolute()
        if path.is_symlink() or not path.exists():
            return None
        if system == 'linux' and (not path.is_file() or path.suffix != '.AppImage'):
            return None
        if system == 'macos' and (not path.is_dir() or path.suffix != '.app'):
            return None
        if system == 'windows' and (not path.is_dir() or
                not (path / 'eightbitdo-updater.exe').is_file() or not (path / 'eightbitdo.exe').is_file()):
            return None
        if system != 'linux' and not Path(sys.executable).resolve().is_relative_to(path.resolve()):
            return None
        return Installation(path.resolve(), 'desktop', system, arch, int(pid))
    return Installation(Path(sys.executable).resolve(), 'cli', system, arch)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.update-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _remove_owned(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _validate_archive(path):
    # tufup delegates extraction to shutil; enforce an application-only tar
    # layout before extraction on Python versions without tar's data filter.
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = member.name.removeprefix('./').rstrip('/')
            if name in {'', '.'} and member.isdir():
                continue
            if name != 'update-manifest.json' and name != 'application' and not name.startswith('application/'):
                raise ValueError('Application archive contains an unrelated path.')
            if '..' in name.split('/') or '\\' in name or ':' in name:
                raise ValueError('Application archive contains an unsafe path.')
            if member.issym() or member.islnk():
                target = member.linkname
                if target.startswith('/') or '\\' in target or ':' in target:
                    raise ValueError('Application archive contains an unsafe link.')
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target) if member.issym() else target)
                if resolved != 'application' and not resolved.startswith('application/'):
                    raise ValueError('Application archive link escapes the application.')
            elif not (member.isfile() or member.isdir()):
                raise ValueError('Application archive contains a special file.')


def _validate_application_links(source):
    if source.is_dir():
        for path in source.rglob('*'):
            if path.is_symlink() and not path.resolve().is_relative_to(source.resolve()):
                raise ValueError('An application payload link escapes the application directory.')


def _transaction_paths(record):
    token = record.get('token', '')
    if record.get('schema') != 1 or not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Invalid application update transaction.')
    destination = Path(record['destination'])
    if not destination.is_absolute() or destination.is_symlink():
        raise ValueError('Invalid application installation path.')
    staged = destination.with_name(f'.{destination.name}.8bitdo-new-{token}')
    backup = destination.with_name(f'.{destination.name}.8bitdo-backup-{token}')
    if staged.is_symlink() or backup.is_symlink():
        raise ValueError('Invalid application update staging path.')
    return destination, staged, backup


def _helper_running(record):
    import psutil
    if type(record.get('helper_pid')) is not int or not isinstance(record.get('helper_created'), (int, float)):
        return False
    try:
        process = psutil.Process(record['helper_pid'])
        return process.create_time() == record['helper_created'] and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _replace_application(state_dir, record):
    destination, staged, backup = _transaction_paths(record)
    if not destination.exists() or not staged.exists() or backup.exists():
        raise RuntimeError('Application update paths changed; recover the pending update before retrying.')
    marker = state_dir / 'app-update-pending.json'
    try:
        os.replace(destination, backup)
        record['phase'] = 'backed_up'
        _write_json(marker, record)
        os.replace(staged, destination)
        record['phase'] = 'installed'
        _write_json(state_dir / 'app-update-last.json', record)
        marker.unlink()
    except BaseException:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        if destination.exists() and not backup.exists():
            marker.unlink(missing_ok=True)
            _remove_owned(staged)
        raise


def _rollback_application(state_dir, record):
    destination, staged, backup = _transaction_paths(record)
    if backup.exists():
        if destination.exists():
            if staged.exists():
                raise RuntimeError('Ambiguous update paths; preserve the backup and reinstall manually.')
            os.replace(destination, staged)
        try:
            os.replace(backup, destination)
        except BaseException:
            if staged.exists() and not destination.exists():
                os.replace(staged, destination)
            raise
    if not destination.exists():
        raise RuntimeError('The original application is missing; reinstall it manually.')
    _remove_owned(staged)
    (state_dir / 'app-update-pending.json').unlink()


def finish_update(state_dir, token):
    """Private detached installer; never exposed through the frontend dispatch."""
    import psutil
    state_dir = Path(state_dir).resolve()
    marker = state_dir / 'app-update-pending.json'
    record = json.loads(marker.read_text())
    if record.get('token') != token:
        raise ValueError('Application update transaction changed.')
    _transaction_paths(record)
    deadline = time.monotonic() + 300
    while True:
        active = False
        for identity in record.get('wait_for', []):
            try:
                process = psutil.Process(identity['pid'])
                active |= process.create_time() == identity['created'] and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                pass
        if not active:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('Close the application, then recover the pending application update.')
        time.sleep(0.25)
    with JobStore(state_dir).lock('application-mutation'):
        current = json.loads(marker.read_text())
        if current.get('token') != token:
            raise ValueError('Application update transaction changed.')
        if current.get('action', 'install') == 'recover':
            _rollback_application(state_dir, current)
        elif current.get('action', 'install') == 'install':
            _replace_application(state_dir, current)
        else:
            raise ValueError('Invalid application update transaction action.')


class AppUpdater:
    def __init__(self, state_dir, *, guard, layout=None, config=None, root=None, current_version=__version__):
        self.state_dir = Path(state_dir)
        self.store = JobStore(self.state_dir)
        self.guard = guard
        self.layout = layout if layout is not None else installation()
        self.unsupported_message = 'Use your package manager or Decky to update this installation.'
        if layout is None and self.state_dir.resolve() != default_state_dir().resolve():
            self.layout = None
            self.unsupported_message = 'Application self-updates require the default state directory. Run without --state-dir.'
        self.config = config
        self.root = root
        self.current_version = current_version

    def _status(self, channel, **values):
        if channel not in {'stable', 'alpha'}:
            raise ValueError('Application update channel must be stable or alpha.')
        result = dict(current_version=self.current_version, channel=channel,
                      platform=self.layout.platform if self.layout else sys.platform,
                      arch=self.layout.arch if self.layout else platform.machine(),
                      available_version=None, release_notes='', state='unsupported',
                      can_apply=False, can_recover=False, restart_required=False,
                      message=self.unsupported_message)
        result.update(values)
        return result

    @contextmanager
    def _client(self):
        from tufup.client import Client
        # Use Python's safe tar policy for tufup's shutil extraction too. This
        # resolves link chains at extraction time, not just lexical tar paths.
        if not hasattr(tarfile, 'data_filter'):
            raise RuntimeError('Application updates require a Python runtime with safe tar extraction support.')
        tarfile.TarFile.extraction_filter = staticmethod(tarfile.data_filter)
        config = self.config or json.loads(files('eightbitdo.resources').joinpath('update-config.json').read_text())
        root = self.root or files('eightbitdo.resources').joinpath('tuf-root.json').read_bytes()
        directory = self.state_dir / 'app-updates' / self.layout.app_name
        metadata, targets = directory / 'metadata', directory / 'targets'
        for path in (metadata, targets):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (metadata / 'root.json').exists():
            (metadata / 'root.json').write_bytes(root)
        target_base = config['target_base_url'].rstrip('/') + '/'
        release_targets = config.get('release_targets', True)

        class ReleaseClient(Client):
            def find_cached_target(self, targetinfo, filepath=None):
                cached = super().find_cached_target(targetinfo, filepath=filepath)
                if cached:
                    _validate_archive(cached)
                return cached

            def download_target(self, targetinfo, filepath=None, target_base_url=None):
                base = target_base
                if release_targets:
                    tag = targetinfo.custom.get('user', {}).get('release_tag', '')
                    if not re.fullmatch(r'v[0-9][A-Za-z0-9.+-]*', tag):
                        raise ValueError('Signed update metadata has an invalid release tag.')
                    base += tag + '/'
                downloaded = super().download_target(targetinfo, filepath=filepath, target_base_url=base)
                _validate_archive(downloaded)
                return downloaded

        with tempfile.TemporaryDirectory(prefix='extract-', dir=directory) as extracted:
            yield ReleaseClient(app_name=self.layout.app_name, app_install_dir=self.layout.path.parent,
                                current_version=self.current_version, metadata_dir=metadata,
                                metadata_base_url=config['metadata_base_url'].rstrip('/') + '/' + self.layout.app_name + '/metadata/',
                                target_dir=targets, target_base_url=target_base, extract_dir=Path(extracted))

    def _check(self, client, channel):
        from tuf.api.exceptions import DownloadError, RepositoryError
        try:
            target = client.check_for_updates(pre='a' if channel == 'alpha' else None, patch=False)
        except (DownloadError, RepositoryError) as error:
            raise RuntimeError('Application update metadata could not be verified. Try again later.') from error
        # tufup catches some refresh errors and returns None. That is not "current".
        if client.new_targets is None:
            raise RuntimeError('Application update metadata could not be downloaded or verified.')
        if target is None:
            return self._status(channel, state='current', message='This application is up to date.')
        expected = dict(schema=1, product=self.layout.product, platform=self.layout.platform,
                        arch=self.layout.arch, version=str(target.version))
        if any(target.custom.get(key) != value for key, value in expected.items()):
            raise ValueError('Signed update target does not match this application, platform, and architecture.')
        writable = os.access(self.layout.path.parent, os.W_OK)
        return self._status(channel, available_version=str(target.version),
                            release_notes=str(target.custom.get('release_notes', '')), state='available',
                            can_apply=writable, message='An application update is available.' if writable else
                            'The installation directory is not writable. Update through your installer.')

    def check(self, channel):
        status = self._status(channel)
        if self.layout is None:
            return status
        marker = self.state_dir / 'app-update-pending.json'
        if marker.exists():
            record = json.loads(marker.read_text())
            running = _helper_running(record)
            return self._status(channel, state='staged' if running else 'interrupted',
                                can_recover=not running, restart_required=True,
                                message='Quit the app to finish installing, then reopen it.' if running else
                                'The application update was interrupted. Confirm recovery to restore the previous application.')
        with self.store.lock('self-update'), self._client() as client:
            return self._check(client, channel)

    def apply(self, channel, version, confirmed=False):
        if confirmed is not True:
            raise ValueError('Explicit confirmation is required to update the application.')
        self._status(channel)
        if self.layout is None:
            raise ValueError('This installation does not support application self-updates.')
        with self.store.lock('self-update'), self.guard():
            if (self.state_dir / 'app-update-pending.json').exists():
                raise RuntimeError('Finish or recover the pending application update first.')
            with self._client() as client:
                status = self._check(client, channel)
                if not status['can_apply'] or status['available_version'] != version:
                    raise ValueError('Check application updates again and confirm the available version.')
                outcome = []

                def install(src_dir, dst_dir):
                    manifest = json.loads((src_dir / 'update-manifest.json').read_text())
                    expected = dict(schema=1, product=self.layout.product, platform=self.layout.platform,
                                    arch=self.layout.arch, version=version)
                    if manifest != expected or {p.name for p in src_dir.iterdir()} != {'application', 'update-manifest.json'}:
                        raise ValueError('The signed application payload has an unexpected layout.')
                    source = src_dir / 'application'
                    if source.is_symlink() or source.is_dir() != self.layout.path.is_dir():
                        raise ValueError('The signed application payload has the wrong installation type.')
                    _validate_application_links(source)
                    outcome.append(self._install(source, version))

                from tuf.api.exceptions import DownloadError, RepositoryError
                try:
                    client.download_and_apply_update(skip_confirmation=True, install=install)
                except (DownloadError, RepositoryError) as error:
                    raise RuntimeError('The signed application update could not be downloaded or verified.') from error
                except tarfile.TarError as error:
                    raise ValueError('The signed application archive could not be extracted safely.') from error
                if not outcome:
                    raise RuntimeError('The application update was not installed.')
                return status | dict(state=outcome[0], can_apply=False, restart_required=True,
                                     message='Quit the app to finish installing, then reopen it.' if outcome[0] == 'staged' else
                                     'Application update installed. Restart the application to use it.')

    def _install(self, source, version):
        token = uuid.uuid4().hex
        record = dict(schema=1, token=token, destination=str(self.layout.path), version=version, phase='staged')
        destination, staged, backup = _transaction_paths(record)
        if source.is_dir():
            shutil.copytree(source, staged, symlinks=True)
            if self.layout.product == 'desktop' and self.layout.platform == 'windows':
                uninstaller = destination / 'uninstall.exe'
                if uninstaller.exists():
                    if uninstaller.is_symlink() or not uninstaller.is_file() or (staged / 'uninstall.exe').exists():
                        raise ValueError('Unexpected Windows uninstaller layout; use the application installer.')
                    # Tauri NSIS registers this installation-specific executable.
                    shutil.copy2(uninstaller, staged / 'uninstall.exe')
        else:
            shutil.copy2(source, staged)
        _write_json(self.state_dir / 'app-update-pending.json', record)
        if self.layout.product != 'desktop' and self.layout.platform != 'windows':
            _replace_application(self.state_dir, record)
            return 'installed'
        self._launch_helper(record)
        return 'staged'

    def _launch_helper(self, record):
        import psutil
        token = record['token']
        pids = {os.getpid(), self.layout.desktop_pid} - {None}
        parent = psutil.Process(os.getpid()).parent()
        if parent is not None and Path(parent.exe()).resolve() == Path(sys.executable).resolve():
            # A one-file bootloader can outlive its Python child while removing
            # extracted files, and still hold Windows executable handles.
            pids.add(parent.pid)
        record['wait_for'] = [dict(pid=pid, created=psutil.Process(pid).create_time()) for pid in pids]
        marker = self.state_dir / 'app-update-pending.json'
        _write_json(marker, record)
        helper_dir = self.state_dir / 'app-updates' / 'helpers'
        helper_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        helper = helper_dir / (token + ('.exe' if self.layout.platform == 'windows' else ''))
        shutil.copy2(sys.executable, helper)
        environment = external_environment()
        environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        options = dict(cwd=self.state_dir, env=environment, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        if self.layout.platform == 'windows':
            options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            options['start_new_session'] = True
        process = subprocess.Popen([str(helper), '--apply-app-update', str(self.state_dir), token], **options)
        record['helper_pid'] = process.pid
        record['helper_created'] = psutil.Process(process.pid).create_time()
        _write_json(marker, record)

    def recover(self, confirmed=False):
        if confirmed is not True:
            raise ValueError('Explicit confirmation is required to recover an application update.')
        if self.layout is None:
            raise ValueError('This installation does not support application update recovery.')
        marker = self.state_dir / 'app-update-pending.json'
        with self.store.lock('self-update'), self.guard():
            if not marker.exists():
                raise ValueError('There is no interrupted application update to recover.')
            record = json.loads(marker.read_text())
            if _helper_running(record):
                raise RuntimeError('The application installer is still running. Quit the app to finish, or wait for it to stop before recovery.')
            destination, staged, backup = _transaction_paths(record)
            if destination != self.layout.path:
                raise ValueError('The pending update belongs to another application installation.')
            if backup.exists() and (self.layout.product == 'desktop' or self.layout.platform == 'windows'):
                record['action'] = 'recover'
                self._launch_helper(record)
                return dict(state='staged', restart_required=True, can_apply=False, can_recover=False,
                            message='Quit the app to finish rolling back the interrupted update, then reopen it.')
            _rollback_application(self.state_dir, record)
        return dict(state='recovered', restart_required=True, can_apply=False,
                    message='The interrupted application update was rolled back. Restart the application.')
