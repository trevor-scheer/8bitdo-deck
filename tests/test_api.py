import asyncio
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from eightbitdo.api import API_VERSION, APIError, UpdaterAPI
from eightbitdo.cli import main


class APITests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.client = UpdaterAPI(service=self.service)

    def test_info_describes_api_without_creating_state_or_initializing_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'unused-state'
            client = UpdaterAPI(state)
            with patch('eightbitdo.service.Service', side_effect=AssertionError('Service initialized')):
                response = client.request('info')
            self.assertFalse(state.exists())
        result = response['result']
        self.assertTrue(response['ok'])
        self.assertEqual(result['api_version'], API_VERSION)
        self.assertIn('app_version', result)
        self.assertIn('confirmed', result['operations']['install_update']['required'])
        self.assertNotIn('run_install', result['operations'])
        self.assertIsNone(client._service)

    def test_version_operation_and_parameter_validation_precede_service_calls(self):
        cases = [
            ('scan_devices', {}, 2, 'unsupported_api_version'),
            ('scan_devices', {}, True, 'unsupported_api_version'),
            ('run_install', {}, 1, 'unknown_operation'),
            ('_spawn', {}, 1, 'unknown_operation'),
            ('get_releases', {}, 1, 'invalid_parameters'),
            ('get_releases', {'model_id': 3}, 1, 'invalid_parameters'),
            ('scan_devices', {'probe': False}, 1, 'invalid_parameters'),
            ('scan_devices', [], 1, 'invalid_parameters'),
        ]
        for operation, params, version, code in cases:
            with self.subTest(operation=operation, params=params, version=version):
                result = self.client.request(operation, params, api_version=version)
                self.assertEqual(result['error']['code'], code)
                self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertEqual(self.service.mock_calls, [])

    def test_all_mutations_with_confirmation_require_exact_true(self):
        for operation, base in (
            ('install_update', {'job_id': 'fixture'}), ('recover_update', {'job_id': 'fixture'}),
            ('install_updates', {'job_ids': ['fixture']}),
            ('setup_recovery_access', {}), ('apply_app_update', {'channel': 'stable', 'version': '1.0.0'}),
            ('recover_app_update', {}),
        ):
            for value in (None, False, 1, 'true'):
                params = dict(base)
                if value is not None:
                    params['confirmed'] = value
                with self.subTest(operation=operation, value=value):
                    response = self.client.request(operation, params)
                    self.assertFalse(response['ok'])
                    self.assertIn(response['error']['code'], {'confirmation_required', 'invalid_parameters'})
        self.assertEqual(self.service.mock_calls, [])

    def test_confirmed_install_preserves_backend_authorization_checks(self):
        self.service.install_update.side_effect = ValueError('Firmware is not qualified')
        result = self.client.request('install_update', {'job_id': 'fixture', 'confirmed': True})
        self.assertEqual(result['error']['code'], 'operation_rejected')
        self.service.install_update.assert_called_once_with(job_id='fixture')
        self.service.run_install.assert_not_called()

    def test_results_keep_existing_shapes_inside_versioned_envelope(self):
        self.service.scan_devices.return_value = {'devices': [], 'warnings': ['fwupd unavailable']}
        result = self.client.request('scan_devices')
        self.assertEqual(result, {'api_version': 1, 'ok': True,
                                 'result': {'devices': [], 'warnings': ['fwupd unavailable']}})
        self.service.prepare_update.return_value = {'job_id': 'created'}
        self.assertEqual(self.client.call('prepare_update', model_id='model', release_id='release'), {'job_id': 'created'})
        self.service.prepare_update.assert_called_once_with(device_id='', model_id='model', release_id='release')

    def test_errors_are_structured_and_call_raises_same_code(self):
        cases = [
            (PermissionError('access'), 'permission_denied'),
            (TimeoutError('reply'), 'timeout'),
            (OSError('device'), 'io_error'),
            (ValueError('target'), 'operation_rejected'),
            (RuntimeError('worker'), 'operation_failed'),
        ]
        for error, code in cases:
            self.service.scan_devices.side_effect = error
            with self.subTest(code=code):
                response = self.client.request('scan_devices')
                self.assertEqual(response, {'api_version': 1, 'ok': False,
                                            'error': {'code': code, 'message': str(error)}})
                with self.assertRaises(APIError) as caught:
                    self.client.call('scan_devices')
                self.assertEqual(caught.exception.code, code)
        self.service.scan_devices.side_effect = None
        self.service.scan_devices.return_value = {'error': 'Catalog unavailable'}
        self.assertEqual(self.client.request('scan_devices')['error']['code'], 'operation_failed')

    def test_non_json_results_fail_without_exposing_the_object(self):
        for value in (Path('/private'), float('nan')):
            self.service.scan_devices.return_value = value
            response = self.client.request('scan_devices')
            self.assertEqual(response['error']['code'], 'internal_error')
            self.assertNotIn('/private', json.dumps(response))

    def test_unexpected_backend_failure_has_a_stable_non_private_error(self):
        self.service.scan_devices.side_effect = KeyError('/private/device-state')
        result = self.client.request('scan_devices')
        self.assertEqual(result['error']['code'], 'internal_error')
        self.assertNotIn('/private', json.dumps(result))

    def test_unsupported_platform_is_explicit_but_info_remains_available(self):
        client = UpdaterAPI()
        with patch('eightbitdo.api.sys.platform', 'freebsd14'):
            self.assertFalse(client.request('info')['result']['platform_supported'])
            self.assertEqual(client.request('scan_devices')['error']['code'], 'unsupported_platform')

    def test_desktop_platforms_expose_detection_without_write_readiness(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform), patch('eightbitdo.api.sys.platform', platform):
                result = UpdaterAPI().call('info')
            self.assertTrue(result['platform_supported'])
            self.assertTrue(result['capabilities']['identity_query'])
            self.assertTrue(result['capabilities']['firmware_download'])
            self.assertFalse(result['capabilities']['native_write'])
            self.assertFalse(result['capabilities']['recovery'])
            self.assertFalse(result['capabilities']['permission_setup'])


class FrontendBoundaryTests(unittest.TestCase):
    def cli(self, args, client=None):
        output, errors = io.StringIO(), io.StringIO()
        with patch('eightbitdo.cli.UpdaterAPI', return_value=client or UpdaterAPI()), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = main(args)
        return status, output.getvalue(), errors.getvalue()

    def test_help_and_version_do_not_construct_api_or_service(self):
        for option in ('--help', '--version', '--licenses'):
            with self.subTest(option=option), \
                    patch('eightbitdo.cli.UpdaterAPI', side_effect=AssertionError('API initialized')), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main([option])
            self.assertEqual(caught.exception.code, 0)

    def test_machine_api_emits_envelope_and_never_runs_invalid_requests(self):
        service = Mock()
        client = UpdaterAPI(service=service)
        for args, code in [
            (['api', 'scan_devices', '--api-version', '2'], 'unsupported_api_version'),
            (['api', 'install_update', '--params', '{"job_id":"fixture"}'], 'confirmation_required'),
            (['api', 'setup_recovery_access', '--params', '{"confirmed":false}'], 'confirmation_required'),
            (['api', 'scan_devices', '--params', '{'], 'invalid_parameters'),
            (['api', 'scan_devices', '--params', '[]'], 'invalid_parameters'),
            (['api', 'scan_devices', '--params', 'null'], 'invalid_parameters'),
            (['api', '_worker'], 'unknown_operation'),
        ]:
            with self.subTest(args=args):
                status, output, errors = self.cli(args, client)
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(output)['error']['code'], code)
                self.assertEqual(errors, '')
        self.assertEqual(service.mock_calls, [])

    def test_cli_info_and_permission_inspection_use_shared_boundary(self):
        status, output, errors = self.cli(['info'])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)['api_version'], 1)
        self.assertEqual(errors, '')
        service = Mock()
        service.get_recovery_access.return_value = {'configured': True}
        status, output, _ = self.cli(['recovery-access'], UpdaterAPI(service=service))
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), {'configured': True})
        service.get_recovery_access.assert_called_once_with()
        service.setup_recovery_access.assert_not_called()

    def test_cli_permission_setup_needs_confirmation_and_then_calls_shared_api(self):
        service = Mock()
        service.setup_recovery_access.return_value = {'configured': True, 'pending': False}
        with self.assertRaises(SystemExit) as caught:
            self.cli(['setup-recovery-access'], UpdaterAPI(service=service))
        self.assertEqual(caught.exception.code, 2)
        service.setup_recovery_access.assert_not_called()
        status, output, _ = self.cli(['setup-recovery-access', '--confirm'], UpdaterAPI(service=service))
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), {'configured': True, 'pending': False})
        service.setup_recovery_access.assert_called_once_with()

    def test_hidden_worker_dispatch_does_not_construct_public_api(self):
        with patch('eightbitdo.worker.run') as worker, \
                patch('eightbitdo.cli.UpdaterAPI', side_effect=AssertionError('API initialized')):
            self.assertEqual(main(['--worker', '/tmp/state', 'a' * 32, 'prepare']), 0)
        worker.assert_called_once_with(Path('/tmp/state'), 'a' * 32, 'prepare')
        with patch('eightbitdo.worker.run') as worker, self.assertRaises(SystemExit):
            main(['--worker', '/tmp/state'])
        worker.assert_not_called()

    def test_decky_adapter_preserves_results_and_requires_confirmation(self):
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location('decky_boundary_test', root / 'main.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plugin = module.Plugin()
        service = Mock()
        service.install_update.return_value = {'state': 'queued'}
        plugin.api = UpdaterAPI(service=service)
        with self.assertRaises(APIError):
            asyncio.run(plugin.install_update('fixture'))
        service.install_update.assert_not_called()
        result = asyncio.run(plugin.install_update('fixture', True))
        self.assertEqual(result, {'state': 'queued'})
        service.install_update.assert_called_once_with(job_id='fixture')


if __name__ == '__main__':
    unittest.main()
