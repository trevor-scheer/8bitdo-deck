import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from eightbitdo.api import UpdaterAPI
from eightbitdo.cli import main


class RecoveryCLITests(unittest.TestCase):
    def run_setup(self, service, *, machine):
        args = (['api', 'setup_recovery_access', '--params', '{"confirmed":true}'] if machine
                else ['setup-recovery-access', '--confirm'])
        output, errors = io.StringIO(), io.StringIO()
        with patch('eightbitdo.cli.UpdaterAPI', return_value=UpdaterAPI(service=service)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = main(args)
        return status, output.getvalue(), errors.getvalue()

    def test_access_setup_waits_for_success_with_the_same_client(self):
        for machine in (False, True):
            with self.subTest(machine=machine), patch('eightbitdo.cli.time.sleep') as sleep:
                service = Mock()
                pending = {'configured': False, 'pending': True}
                complete = {'configured': True, 'pending': False, 'message': 'Ready'}
                service.setup_recovery_access.return_value = pending
                service.get_recovery_access.side_effect = [pending, complete]
                status, output, errors = self.run_setup(service, machine=machine)
                self.assertEqual(status, 0)
                self.assertEqual(errors, '')
                expected = {'api_version': 1, 'ok': True, 'result': complete} if machine else complete
                self.assertEqual(json.loads(output), expected)
                service.setup_recovery_access.assert_called_once_with()
                self.assertEqual(service.get_recovery_access.call_count, 2)
                self.assertEqual(sleep.call_count, 2)

    def test_access_setup_denial_is_a_failure_in_both_cli_modes(self):
        for machine in (False, True):
            with self.subTest(machine=machine), patch('eightbitdo.cli.time.sleep'):
                service = Mock()
                service.setup_recovery_access.return_value = {'configured': False, 'pending': True}
                message = 'System authorization did not complete.'
                service.get_recovery_access.return_value = {'configured': False, 'pending': False, 'message': message}
                status, output, errors = self.run_setup(service, machine=machine)
                self.assertEqual(status, 1)
                if machine:
                    self.assertEqual(json.loads(output), {'api_version': 1, 'ok': False,
                                                         'error': {'code': 'operation_failed', 'message': message}})
                    self.assertEqual(errors, '')
                else:
                    self.assertEqual(output, '')
                    self.assertEqual(json.loads(errors), {'error': message})
                service.setup_recovery_access.assert_called_once_with()
                service.get_recovery_access.assert_called_once_with()

    def test_access_setup_timeout_and_interruption_do_not_claim_cancellation(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), \
                    patch('eightbitdo.cli.time.monotonic', side_effect=[0, 0 if interrupted else 301]), \
                    patch('eightbitdo.cli.time.sleep', side_effect=KeyboardInterrupt if interrupted else None):
                service = Mock()
                service.setup_recovery_access.return_value = {'configured': False, 'pending': True}
                status, output, errors = self.run_setup(service, machine=True)
                self.assertEqual(status, 1)
                self.assertEqual(errors, '')
                response = json.loads(output)
                self.assertFalse(response['ok'])
                self.assertEqual(response['error']['code'], 'operation_failed' if interrupted else 'timeout')
                self.assertIn('may still be', response['error']['message'])
                service.get_recovery_access.assert_not_called()
                service.setup_recovery_access.assert_called_once_with()

    def test_check_only_invokes_read_only_service_method(self):
        service = Mock()
        service.check_recovery.return_value = {'state': 'blocked', 'recovery_ready': True}
        with patch('eightbitdo.cli.UpdaterAPI', return_value=UpdaterAPI(service=service)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['check-recovery', 'fixture']), 0)
        service.check_recovery.assert_called_once_with(job_id='fixture')
        service.recover_update.assert_not_called()
        service.install_update.assert_not_called()

    def test_recovery_requires_explicit_confirmation(self):
        service = Mock()
        with patch('eightbitdo.cli.UpdaterAPI', return_value=UpdaterAPI(service=service)), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_status:
                main(['recover', 'fixture'])
        self.assertEqual(exit_status.exception.code, 2)
        service.recover_update.assert_not_called()

    def test_confirmed_recovery_passes_confirmation_to_service(self):
        service = Mock()
        service.recover_update.return_value = {'state': 'queued'}
        with patch('eightbitdo.cli.UpdaterAPI', return_value=UpdaterAPI(service=service)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['recover', 'fixture', '--confirm']), 0)
        service.recover_update.assert_called_once_with(job_id='fixture', confirmed=True)
        service.install_update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
