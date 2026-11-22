import contextlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import tarfile
import threading
import unittest
from unittest.mock import Mock, patch

from eightbitdo.api import UpdaterAPI
from eightbitdo.cli import main
from eightbitdo.selfupdate import AppUpdater, Installation, _transaction_paths, _validate_application_links, _validate_archive, _write_json, finish_update
from eightbitdo.service import Service
from scripts.tuf_repo import add_bundle, initialize, open_repository, refresh


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


class SignedUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.root = Path(cls.fixture.name)
        cls.app_name = 'eightbitdo-cli-linux-x86_64'
        cls.repository = cls.root / 'repositories' / cls.app_name
        cls.keys = cls.root / 'keys'
        initialize(cls.repository, cls.keys, cls.app_name)
        source = cls.root / 'new-app'
        source.write_bytes(b'new application')
        source.chmod(0o755)
        add_bundle(cls.repository, cls.keys, cls.app_name, source, '1.1', 'v1.1', 'Signed stable notes')
        source.write_bytes(b'alpha application')
        add_bundle(cls.repository, cls.keys, cls.app_name, source, '1.2a1', 'v1.2-alpha.1', 'Signed alpha notes')

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repos = self.directory / 'repositories'
        shutil.copytree(self.root / 'repositories', self.repos)
        self.repo = self.repos / self.app_name
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(self.repos)))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}/'
        self.app = self.directory / 'eightbitdo'
        self.app.write_bytes(b'original application')
        self.app.chmod(0o755)
        self.neighbor = self.directory / 'unrelated.txt'
        self.neighbor.write_text('preserve me')
        self.state = self.directory / 'state'
        coordination = patch('eightbitdo.service.default_state_dir', return_value=self.state)
        coordination.start()
        self.addCleanup(coordination.stop)
        host = Mock()
        host.worker_running.return_value = False
        self.service = Service(self.state, platform=host)
        self.updater = self.client()

    def client(self, version='1.0'):
        return AppUpdater(self.state, guard=self.service.app_update_guard,
                          layout=Installation(self.app, 'cli', 'linux', 'x86_64'), current_version=version,
                          root=(self.repo / 'metadata/root.json').read_bytes(),
                          config=dict(metadata_base_url=self.base,
                                      target_base_url=self.base + self.app_name + '/targets/', release_targets=False))

    def test_signed_channel_selection_and_no_update(self):
        stable = self.updater.check('stable')
        self.assertEqual(stable['available_version'], '1.1')
        self.assertEqual(stable['release_notes'], 'Signed stable notes')
        self.assertTrue(stable['can_apply'])
        self.assertEqual(self.updater.check('alpha')['available_version'], '1.2a1')
        self.assertEqual(self.client('1.2').check('stable')['state'], 'current')
        self.assertEqual(self.app.read_bytes(), b'original application')
        with self.assertRaises(ValueError):
            self.updater.check('nightly')

    def test_verified_apply_replaces_only_application_and_keeps_backup(self):
        result = self.updater.apply('stable', '1.1', True)
        self.assertEqual(result['state'], 'installed')
        self.assertTrue(result['restart_required'])
        self.assertEqual(self.app.read_bytes(), b'new application')
        self.assertEqual(self.neighbor.read_text(), 'preserve me')
        self.assertFalse((self.state / 'app-update-pending.json').exists())
        backups = list(self.directory.glob('.eightbitdo.8bitdo-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'original application')
        if os.name != 'nt':
            self.assertTrue(self.app.stat().st_mode & 0o100)

    def test_apply_requires_confirmation_and_exact_reviewed_version(self):
        for confirmation in (False, 1, 'true'):
            with self.subTest(confirmation=confirmation), self.assertRaises(ValueError):
                self.updater.apply('stable', '1.1', confirmation)
        with self.assertRaisesRegex(ValueError, 'confirm the available version'):
            self.updater.apply('stable', '1.0', True)
        self.assertEqual(self.app.read_bytes(), b'original application')

    def test_tampered_archive_is_rejected_before_installation(self):
        (self.repo / 'targets' / f'{self.app_name}-1.1.tar.gz').write_bytes(b'tampered')
        with self.assertRaisesRegex(RuntimeError, 'verified'):
            self.updater.apply('stable', '1.1', True)
        self.assertEqual(self.app.read_bytes(), b'original application')
        self.assertFalse((self.state / 'app-update-pending.json').exists())

    def test_tampered_metadata_is_not_reported_as_current(self):
        timestamp = self.repo / 'metadata/timestamp.json'
        document = json.loads(timestamp.read_text())
        document['signatures'][0]['sig'] = '0' * 128
        timestamp.write_text(json.dumps(document))
        with self.assertRaisesRegex(RuntimeError, 'verified'):
            self.updater.check('stable')
        self.assertEqual(self.app.read_bytes(), b'original application')

    def test_valid_signature_does_not_override_platform_identity(self):
        with open_repository(self.repo, self.keys, self.app_name) as repo:
            target = repo.roles.targets.signed.targets[f'{self.app_name}-1.1.tar.gz']
            target.custom['user']['arch'] = 'aarch64'
            repo.publish_changes(private_key_dirs=[self.keys])
        with self.assertRaisesRegex(ValueError, 'platform, and architecture'):
            self.updater.check('stable')

    def test_archive_preflight_rejects_paths_and_links_outside_application(self):
        for name, link in [('application/../../neighbor', ''), ('unrelated.txt', ''), ('application/link', '../../neighbor')]:
            archive = self.directory / 'malformed.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                member = tarfile.TarInfo(name)
                if link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = link
                stream.addfile(member)
            with self.subTest(name=name), self.assertRaises(ValueError):
                _validate_archive(archive)

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink extraction policy')
    def test_actual_tufup_extraction_rejects_symlink_chain_escape(self):
        archive = self.repo / 'targets' / f'{self.app_name}-1.1.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            directory = tarfile.TarInfo('application/d')
            directory.type = tarfile.DIRTYPE
            stream.addfile(directory)
            for name in ('application/d/up', 'application/d/up/up', 'application/d/up/up/up'):
                member = tarfile.TarInfo(name)
                member.type = tarfile.SYMTYPE
                member.linkname = '..'
                stream.addfile(member)
            payload = tarfile.TarInfo('application/d/up/up/up/escaped')
            payload.size = 7
            stream.addfile(payload, io.BytesIO(b'escaped'))
        # Each link is lexically inside the application. Only extraction-time
        # resolution sees the chain escape, including through shutil/tufup.
        _validate_archive(archive)
        with open_repository(self.repo, self.keys, self.app_name) as repo:
            custom = repo.roles.targets.signed.targets[archive.name].custom
            repo.roles.add_or_update_target(archive, custom=custom)
            repo.publish_changes(private_key_dirs=[self.keys])
        with self.assertRaisesRegex(ValueError, 'extracted safely'):
            self.updater.apply('stable', '1.1', True)
        self.assertEqual(list(self.state.rglob('escaped')), [])
        self.assertEqual(self.app.read_bytes(), b'original application')

    @unittest.skipIf(os.name == 'nt', 'macOS framework symlinks')
    def test_resolved_link_policy_preserves_framework_links_but_rejects_parent_escape(self):
        application = self.directory / 'application'
        framework = application / 'Contents/Frameworks/Example.framework'
        version = framework / 'Versions/A'
        version.mkdir(parents=True)
        (version / 'Example').write_bytes(b'framework binary')
        (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
        (framework / 'Example').symlink_to('Versions/Current/Example')
        _validate_application_links(application)
        (application / 'up').symlink_to('..', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'escapes the application'):
            _validate_application_links(application)

    def test_detached_installer_waits_for_original_process_before_replacing(self):
        record = dict(schema=1, token='b' * 32, destination=str(self.app), phase='staged', version='1.1',
                      wait_for=[dict(pid=12345, created=123.0)])
        _, staged, _ = _transaction_paths(record)
        staged.write_bytes(b'new application')
        _write_json(self.state / 'app-update-pending.json', record)
        process = Mock()
        process.create_time.return_value = 123.0
        process.is_running.side_effect = [True, False]
        process.status.return_value = 'running'

        def waiting(_):
            self.assertEqual(self.app.read_bytes(), b'original application')
            with self.assertRaisesRegex(RuntimeError, 'pending'):
                with self.service._queue_guard():
                    pass

        with patch('psutil.Process', return_value=process), patch('eightbitdo.selfupdate.time.sleep', side_effect=waiting):
            finish_update(self.state, record['token'])
        self.assertEqual(self.app.read_bytes(), b'new application')
        self.assertFalse((self.state / 'app-update-pending.json').exists())

    def test_windows_staging_preserves_nsis_uninstaller_and_launches_independent_helper(self):
        installed = self.directory / 'desktop-app'
        installed.mkdir()
        (installed / 'eightbitdo-updater.exe').write_bytes(b'old frontend')
        backend = installed / 'eightbitdo.exe'
        backend.write_bytes(b'old backend')
        (installed / 'uninstall.exe').write_bytes(b'installation-specific uninstaller')
        incoming = self.directory / 'desktop-update'
        incoming.mkdir()
        (incoming / 'eightbitdo-updater.exe').write_bytes(b'new frontend')
        (incoming / 'eightbitdo.exe').write_bytes(b'new backend')
        updater = AppUpdater(self.state, guard=self.service.app_update_guard,
                             layout=Installation(installed, 'desktop', 'windows', 'x86_64', os.getpid()))
        with patch('eightbitdo.selfupdate.sys.executable', str(backend)), \
                patch('eightbitdo.selfupdate.subprocess.CREATE_NEW_PROCESS_GROUP', 512, create=True), \
                patch('eightbitdo.selfupdate.subprocess.DETACHED_PROCESS', 8, create=True), \
                patch('eightbitdo.selfupdate.subprocess.Popen') as launch:
            launch.return_value.pid = os.getpid()
            self.assertEqual(updater._install(incoming, '1.1'), 'staged')
        record = json.loads((self.state / 'app-update-pending.json').read_text())
        _, staged, _ = _transaction_paths(record)
        self.assertEqual((staged / 'uninstall.exe').read_bytes(), b'installation-specific uninstaller')
        self.assertEqual((installed / 'eightbitdo-updater.exe').read_bytes(), b'old frontend')
        self.assertFalse(updater.check('stable')['can_recover'])
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            updater.recover(True)
        command = launch.call_args.args[0]
        self.assertEqual(command[1:], ['--apply-app-update', str(self.state), record['token']])
        self.assertEqual(launch.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertFalse(Path(command[0]).is_relative_to(installed))
        record['wait_for'] = []
        _write_json(self.state / 'app-update-pending.json', record)
        finish_update(self.state, record['token'])
        self.assertEqual((installed / 'eightbitdo-updater.exe').read_bytes(), b'new frontend')
        self.assertEqual((installed / 'uninstall.exe').read_bytes(), b'installation-specific uninstaller')

    def test_firmware_jobs_and_queue_races_block_application_changes(self):
        job = self.service.store.create('', 'fixture', 'fixture')
        with self.assertRaisesRegex(RuntimeError, 'firmware jobs'):
            self.updater.apply('stable', '1.1', True)
        job['state'] = 'complete'
        self.service.store.save(job)
        with self.service.store.lock('application-mutation'), self.assertRaises(RuntimeError):
            self.updater.apply('stable', '1.1', True)
        self.assertEqual(self.app.read_bytes(), b'original application')

    def test_failed_swap_restores_original_and_unlocks_firmware(self):
        replace = os.replace

        def fail_install(source, target):
            if '.8bitdo-new-' in str(source) and Path(target) == self.app:
                raise OSError('simulated disk failure')
            return replace(source, target)

        with patch('eightbitdo.selfupdate.os.replace', side_effect=fail_install), self.assertRaises(OSError):
            self.updater.apply('stable', '1.1', True)
        self.assertEqual(self.app.read_bytes(), b'original application')
        self.assertFalse((self.state / 'app-update-pending.json').exists())
        with self.service._queue_guard():
            pass

    def test_interrupted_transaction_blocks_jobs_until_confirmed_rollback(self):
        record = dict(schema=1, token='a' * 32, destination=str(self.app), phase='backed_up', version='1.1')
        destination, staged, backup = _transaction_paths(record)
        os.replace(destination, backup)
        staged.write_bytes(b'new application')
        _write_json(self.state / 'app-update-pending.json', record)
        self.assertTrue(self.updater.check('stable')['can_recover'])
        with self.assertRaisesRegex(RuntimeError, 'pending'):
            with self.service._queue_guard():
                pass
        with self.assertRaises(ValueError):
            self.updater.recover(False)
        self.assertEqual(self.updater.recover(True)['state'], 'recovered')
        self.assertEqual(self.app.read_bytes(), b'original application')
        self.assertFalse(staged.exists())
        with self.service._queue_guard():
            pass

    def test_windows_rollback_defers_replacement_until_application_exit(self):
        record = dict(schema=1, token='c' * 32, destination=str(self.app), phase='installed', version='1.1')
        _, staged, backup = _transaction_paths(record)
        backup.write_bytes(b'previous application')
        _write_json(self.state / 'app-update-pending.json', record)
        updater = AppUpdater(self.state, guard=self.service.app_update_guard,
                             layout=Installation(self.app, 'cli', 'windows', 'x86_64'))
        with patch('eightbitdo.selfupdate.subprocess.CREATE_NEW_PROCESS_GROUP', 512, create=True), \
                patch('eightbitdo.selfupdate.subprocess.DETACHED_PROCESS', 8, create=True), \
                patch('eightbitdo.selfupdate.subprocess.Popen') as launch:
            launch.return_value.pid = os.getpid()
            result = updater.recover(True)
        self.assertEqual(result['state'], 'staged')
        self.assertEqual(self.app.read_bytes(), b'original application')
        pending = json.loads((self.state / 'app-update-pending.json').read_text())
        self.assertEqual(pending['action'], 'recover')
        pending['wait_for'] = []
        _write_json(self.state / 'app-update-pending.json', pending)
        finish_update(self.state, record['token'])
        self.assertEqual(self.app.read_bytes(), b'previous application')
        self.assertFalse(staged.exists())
        self.assertFalse((self.state / 'app-update-pending.json').exists())

    def test_online_refresh_does_not_need_private_root_key(self):
        online = self.directory / 'online-keys'
        shutil.copytree(self.keys, online)
        (online / 'root').unlink()
        before = json.loads((self.repo / 'metadata/targets.json').read_text())['signed']['version']
        refresh(self.repo, online, self.app_name)
        after = json.loads((self.repo / 'metadata/targets.json').read_text())['signed']['version']
        self.assertGreaterEqual(after, before)
        self.assertEqual(self.updater.check('stable')['available_version'], '1.1')


class SelfUpdateCLITests(unittest.TestCase):
    def test_commands_use_versioned_boundary_and_confirmation(self):
        service = Mock()
        service.check_app_update.return_value = {'state': 'current'}
        service.apply_app_update.return_value = {'state': 'installed'}
        service.recover_app_update.return_value = {'state': 'recovered'}
        with patch('eightbitdo.cli.UpdaterAPI', return_value=UpdaterAPI(service=service)), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['app-update-check', '--channel', 'alpha']), 0)
            with self.assertRaises(SystemExit):
                main(['app-update-apply', '1.1'])
            service.apply_app_update.assert_not_called()
            self.assertEqual(main(['app-update-apply', '1.1', '--confirm']), 0)
            self.assertEqual(main(['app-update-recover', '--confirm']), 0)
        service.check_app_update.assert_called_once_with(channel='alpha')
        service.apply_app_update.assert_called_once_with(channel='stable', version='1.1', confirmed=True)
        service.recover_app_update.assert_called_once_with(confirmed=True)

    def test_source_installation_is_unsupported_without_loading_tufup(self):
        with tempfile.TemporaryDirectory() as directory, patch('eightbitdo.selfupdate.frozen_application', return_value=False):
            updater = AppUpdater(Path(directory), guard=contextlib.nullcontext)
            with patch.object(updater, '_client', side_effect=AssertionError('loaded updater')):
                self.assertEqual(updater.check('stable')['state'], 'unsupported')

    def test_noncanonical_state_directory_cannot_enable_binary_self_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('eightbitdo.selfupdate.installation', return_value=Installation(root / 'app', 'cli', 'linux', 'x86_64')), \
                    patch('eightbitdo.selfupdate.default_state_dir', return_value=root / 'canonical'):
                updater = AppUpdater(root / 'custom', guard=contextlib.nullcontext)
            with patch.object(updater, '_client', side_effect=AssertionError('loaded updater')):
                result = updater.check('stable')
                self.assertEqual(result['state'], 'unsupported')
                self.assertIn('default state directory', result['message'])
                with self.assertRaises(ValueError):
                    updater.apply('stable', '1.1', True)
