import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from eightbitdo.cli import main


class RecoveryCLITests(unittest.TestCase):
    def test_check_only_invokes_read_only_service_method(self):
        service = Mock()
        service.check_recovery.return_value = {'state': 'blocked', 'recovery_ready': True}
        with patch('eightbitdo.cli.Service', return_value=service), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['check-recovery', 'fixture']), 0)
        service.check_recovery.assert_called_once_with('fixture')
        service.recover_update.assert_not_called()
        service.install_update.assert_not_called()

    def test_recovery_requires_explicit_confirmation(self):
        service = Mock()
        with patch('eightbitdo.cli.Service', return_value=service), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_status:
                main(['recover', 'fixture'])
        self.assertEqual(exit_status.exception.code, 2)
        service.recover_update.assert_not_called()

    def test_confirmed_recovery_passes_confirmation_to_service(self):
        service = Mock()
        service.recover_update.return_value = {'state': 'queued'}
        with patch('eightbitdo.cli.Service', return_value=service), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['recover', 'fixture', '--confirm']), 0)
        service.recover_update.assert_called_once_with('fixture', confirmed=True)
        service.install_update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
