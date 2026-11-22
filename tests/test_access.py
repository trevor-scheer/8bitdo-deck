import sys
import hashlib
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from eightbitdo.access import (
    RecoveryAccess, RULE_TEXT, STEAMOS_RULE_LINES, _trusted_text,
    recovery_access_configured, steamos_recovery_access_available,
)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux host integration")
class RecoveryAccessTests(unittest.TestCase):
    def test_steamos_existing_policy_needs_no_installer_or_receipt(self):
        policy = '''# USB devices and topological children
SUBSYSTEMS=="usb", TAG+="uaccess"
# HID devices over hidraw
KERNEL=="hidraw*", TAG+="uaccess"
# Steam Controller udev write access
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
'''
        def trusted_text(path):
            return policy if str(path) == '/usr/lib/udev/rules.d/70-steam-jupiter-input.rules' else None
        with patch('eightbitdo.access._trusted_text', side_effect=trusted_text), \
                patch('eightbitdo.access.os.path.lexists', side_effect=lambda p: str(p).startswith('/usr/lib/')), \
                patch('subprocess.Popen') as spawn:
            access = RecoveryAccess(Path('/unused'))
            self.assertTrue(access.status()['configured'])
            self.assertTrue(access.setup()['configured'])
            spawn.assert_not_called()

    def test_local_override_masks_the_distro_policy_even_when_unreadable(self):
        policy = '\n'.join(STEAMOS_RULE_LINES)
        for directory in ('/etc', '/run', '/usr/local/lib'):
            for override in (None, '', '# disabled', 'GOTO="end"\n' + policy):
                def trusted_text(path):
                    return policy if str(path).startswith('/usr/lib/') else override
                with self.subTest(directory=directory, override=override), \
                        patch('eightbitdo.access.os.path.lexists', side_effect=lambda p: str(p).startswith((directory + '/', '/usr/lib/'))), \
                        patch('eightbitdo.access._trusted_text', side_effect=trusted_text):
                    self.assertFalse(steamos_recovery_access_available())

    def test_missing_commented_or_conditional_policy_is_not_accepted(self):
        policy = '\n'.join(STEAMOS_RULE_LINES)
        for text in (None, '# ' + policy.replace('\n', '\n# '), 'GOTO="end"\n' + policy,
                     policy.replace('KERNEL=="hidraw*"', 'KERNEL=="hidraw0"')):
            with self.subTest(text=text), \
                    patch('eightbitdo.access.os.path.lexists', return_value=True), \
                    patch('eightbitdo.access._trusted_text', return_value=text):
                self.assertFalse(steamos_recovery_access_available())

    def test_installer_and_readiness_use_identical_scoped_rules(self):
        script = (Path(__file__).resolve().parent.parent / 'eightbitdo/resources/install-udev-rules.sh').read_text()
        embedded = script.split("<<'RULES'\n", 1)[1].split('\nRULES\n', 1)[0] + '\n'
        self.assertEqual(embedded, RULE_TEXT)
        self.assertEqual(RULE_TEXT.count('ATTRS{idVendor}=="2dc8"'), 2)
        for pid in ('3208', '3002'):
            self.assertIn(f'ATTRS{{idProduct}}=="{pid}"', RULE_TEXT)
        self.assertNotIn('0666', RULE_TEXT)

    def test_rule_without_completed_install_receipt_is_not_ready(self):
        digest = hashlib.sha256(RULE_TEXT.encode()).hexdigest() + '\n'
        for texts, expected in [([RULE_TEXT, None], False), ([RULE_TEXT, 'wrong'], False),
                                ([RULE_TEXT, digest], True), (['different', digest], False)]:
            with self.subTest(texts=texts), \
                    patch('eightbitdo.access.steamos_recovery_access_available', return_value=False), \
                    patch('eightbitdo.access._trusted_text', side_effect=texts):
                self.assertEqual(recovery_access_configured(), expected)

    def test_non_root_writable_and_symlink_rules_are_untrusted(self):
        path = Mock(spec=Path)
        path.read_text.return_value = RULE_TEXT
        for uid, mode in [(1000, stat.S_IFREG | 0o644), (0, stat.S_IFREG | 0o664),
                          (0, stat.S_IFLNK | 0o777)]:
            path.lstat.return_value = SimpleNamespace(st_uid=uid, st_mode=mode)
            self.assertIsNone(_trusted_text(path))
        path.read_text.assert_not_called()
        path.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)
        self.assertEqual(_trusted_text(path), RULE_TEXT)

    def test_setup_uses_normal_authorization_and_does_not_duplicate_pending_prompt(self):
        with tempfile.TemporaryDirectory() as root, \
                patch('eightbitdo.access.recovery_access_configured', return_value=False), \
                patch('shutil.which', return_value='/usr/bin/pkexec'), \
                patch('subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value = None
            access = RecoveryAccess(Path(root))
            self.assertTrue(access.setup()['pending'])
            self.assertTrue(access.setup()['pending'])
            spawn.assert_called_once()
            command = spawn.call_args.args[0]
            self.assertEqual(command[:3], ['/usr/bin/pkexec', '--disable-internal-agent', '/bin/bash'])
            self.assertTrue(Path(command[3]).is_relative_to(Path(root) / 'resources'))
            self.assertEqual(Path(command[3]).read_bytes(), (Path(__file__).resolve().parent.parent / 'eightbitdo/resources/install-udev-rules.sh').read_bytes())

    def test_staged_helper_survives_bundle_removal_and_is_private(self):
        with tempfile.TemporaryDirectory() as root:
            resource = Mock()
            resource.joinpath.return_value.read_bytes.return_value = b"#!/bin/bash\necho fixture\n"
            with patch('eightbitdo.access.files', return_value=resource):
                script = RecoveryAccess(Path(root))._stage_installer()
            self.assertTrue(script.is_relative_to(Path(root) / 'resources'))
            self.assertEqual(script.read_bytes(), b"#!/bin/bash\necho fixture\n")
            self.assertEqual(script.stat().st_mode & 0o777, 0o600)
            self.assertIn(hashlib.sha256(script.read_bytes()).hexdigest(), script.name)

    def test_declined_authorization_stays_unconfigured(self):
        with patch('eightbitdo.access.recovery_access_configured', return_value=False):
            access = RecoveryAccess(Path('/unused'))
            access._process = Mock(returncode=126)
            access._process.poll.return_value = 126
            status = access.status()
        self.assertFalse(status['configured'])
        self.assertFalse(status['pending'])
        self.assertIn('authorization did not complete', status['message'])


if __name__ == '__main__':
    unittest.main()
