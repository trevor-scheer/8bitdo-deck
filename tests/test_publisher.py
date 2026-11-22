import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts.tuf_repo import initialize

ROOT = Path(__file__).resolve().parent.parent
with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
    publisher = importlib.import_module('publish_updates')


class PublisherNotesTests(unittest.TestCase):
    def test_prepare_requires_notes_before_accessing_signing_keys(self):
        with patch.object(sys, 'argv', ['publish_updates.py', 'prepare', '--metadata-checkout', 'unused',
                                       '--inputs', 'unused', '--release-assets', 'unused', '--tag', 'v0.3.0-alpha.1']), \
                patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as caught:
            publisher.main()
        self.assertEqual(caught.exception.code, 2)

    def test_prepare_signs_exact_reviewed_notes_without_publishing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            name = 'eightbitdo-cli-linux-x86_64'
            metadata = directory / 'metadata-checkout'
            keys = directory / 'keys'
            initialize(metadata / name, keys, name)
            secret = json.dumps({filename: (keys / filename).read_text() for filename in publisher.KEY_FILES})
            inputs = directory / 'inputs'
            inputs.mkdir()
            application = directory / 'app'
            application.write_bytes(b'fixture application, never executed')
            with tarfile.open(inputs / (name + '.tar.gz'), 'w:gz') as archive:
                archive.add(application, arcname='application')
            assets = directory / 'release-assets'
            notes_file = ROOT / 'docs/releases/0.3.0-alpha.1.md'
            argv = ['publish_updates.py', 'prepare', '--metadata-checkout', str(metadata),
                    '--inputs', str(inputs), '--release-assets', str(assets), '--tag', 'v0.3.0-alpha.1',
                    '--notes-file', str(notes_file)]
            with patch.object(sys, 'argv', argv), patch.object(publisher, 'REPOSITORIES', (name,)), \
                    patch.dict(os.environ, {'EIGHTBITDO_TUF_SIGNING_KEYS': secret}), \
                    contextlib.redirect_stdout(io.StringIO()):
                publisher.main()
            targets = json.loads((metadata / name / 'metadata/targets.json').read_text())['signed']['targets']
            self.assertEqual(len(targets), 1)
            archive_name, target = next(iter(targets.items()))
            self.assertEqual(target['custom']['user']['release_notes'], notes_file.read_text(encoding='utf-8'))
            self.assertEqual(target['custom']['user']['release_tag'], 'v0.3.0-alpha.1')
            self.assertTrue((assets / archive_name).is_file())
            self.assertFalse((assets / 'root').exists())


if __name__ == '__main__':
    unittest.main()
