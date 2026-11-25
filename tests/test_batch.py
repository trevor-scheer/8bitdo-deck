import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from eightbitdo.api import UpdaterAPI
from eightbitdo.service import Service
from eightbitdo.worker import run


class BatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = Mock()
        self.host.worker_running.return_value = False
        self.host.recovery_access.return_value.status.return_value = {'configured': True}
        self.service = Service(self.root, coordination_dir=self.root, platform=self.host)

    def ready(self, target='usb-' + 'a' * 16, *, backend='hid'):
        job = self.service.store.create(target, 'ultimate2-receiver', 'release1')
        job.update(state='ready', can_install=True, backend=backend, version='1.06',
                   target_label=target + ' → 1.06')
        self.service.store.save(job)
        return job

    def queue(self, *jobs):
        return self.service.install_updates([job['id'] for job in jobs])

    def dispatch(self, parent):
        with patch('eightbitdo.worker.Service', return_value=self.service), \
                patch('eightbitdo.worker.process_start', return_value='fixture-start'):
            run(self.root, parent['id'], 'batch')

    def complete(self, job):
        job.update(state='complete', progress=100, can_install=False, message='Verified.')
        self.service.store.save(job)

    def test_queue_is_durable_claimed_and_inhibited_before_dispatch(self):
        first, second = self.ready(), self.ready('usb-' + 'b' * 16)
        parent = self.queue(first, second)
        self.assertEqual(parent['state'], 'queued')
        self.assertFalse(parent['can_install'])
        self.assertEqual([j['id'] for j in parent['jobs']], [first['id'], second['id']])
        self.host.worker_launch.assert_called_once_with(self.root, parent['id'], 'batch', inhibit=True)
        for item in (first, second):
            job = self.service.store.read(item['id'])
            self.assertEqual(job['batch_id'], parent['id'])
            self.assertEqual(job['worker_action'], 'install')
            self.assertFalse(job['can_install'])
            with self.assertRaises(ValueError):
                self.service.install_update(item['id'])
        reopened = Service(self.root, coordination_dir=self.root, platform=self.host)
        self.assertEqual(reopened.get_update_queue()['id'], parent['id'])
        self.assertNotIn('artifact_path', str(reopened.get_update_queue()))

    def test_no_queue_returns_null_without_creating_pointer(self):
        self.assertIsNone(self.service.get_update_queue())
        self.assertFalse((self.root / 'latest-update-queue.json').exists())

    def test_confirmation_is_required_and_forwarded_without_review_screen(self):
        job = self.ready()
        api = UpdaterAPI(service=self.service)
        for confirmation in (None, False, 1, 'true'):
            params = {'job_ids': [job['id']]}
            if confirmation is not None:
                params['confirmed'] = confirmation
            self.assertFalse(api.request('install_updates', params)['ok'])
        self.host.start_worker.assert_not_called()
        result = api.call('install_updates', job_ids=[job['id']], confirmed=True)
        self.assertEqual(api.call('get_update_queue')['id'], result['id'])

    def test_invalid_queue_does_not_claim_jobs_or_launch(self):
        ready = self.ready()
        other = self.ready('usb-' + 'b' * 16)
        other.update(state='blocked', can_install=False)
        self.service.store.save(other)
        for ids in ([], [ready['id']] * 2, ['../escape'], [1], 'not-a-list',
                    [ready['id'], other['id']], [ready['id']] * 33):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.service.install_updates(ids)
        self.assertTrue(self.service.store.read(ready['id'])['can_install'])
        self.assertIsNone(self.service.get_update_queue())
        self.host.start_worker.assert_not_called()

    def test_recovery_access_and_platform_requirements_are_preserved(self):
        job = self.ready()
        self.host.recovery_access.return_value.status.return_value = {'configured': False}
        with self.assertRaisesRegex(ValueError, 'recovery access'):
            self.queue(job)
        self.host.require_write_support.side_effect = RuntimeError('unsupported platform')
        with self.assertRaisesRegex(RuntimeError, 'unsupported platform'):
            self.queue(job)
        self.host.start_worker.assert_not_called()

    def test_duplicate_device_or_shared_radio_transport_is_rejected(self):
        receiver = self.ready()
        duplicate = self.ready()
        paired = self.ready('paired-' + 'a' * 16, backend='wireless')
        for other in (duplicate, paired):
            with self.assertRaisesRegex(ValueError, 'physical device or receiver'):
                self.queue(receiver, other)
        self.host.start_worker.assert_not_called()
        self.assertTrue(self.service.store.read(receiver['id'])['can_install'])

    def test_a_second_queue_or_retry_cannot_start_while_first_is_pending(self):
        first, second = self.ready(), self.ready('usb-' + 'b' * 16)
        parent = self.queue(first)
        for selected in (first, second):
            with self.assertRaisesRegex(ValueError, 'already running'):
                self.queue(selected)
        self.assertEqual(self.service.get_update_queue()['id'], parent['id'])
        self.assertEqual(self.host.start_worker.call_count, 1)

    def test_worker_runs_in_order_holds_locks_and_does_not_need_frontend(self):
        first, second = self.ready(), self.ready('usb-' + 'b' * 16)
        parent = self.queue(first, second)
        calls = []

        def install(job):
            calls.append(job['id'])
            with self.assertRaises(RuntimeError):
                with self.service.store.lock(job['id']):
                    self.fail('Child job was not locked')
            with self.assertRaises(RuntimeError):
                with self.service.app_update_guard():
                    self.fail('Application replacement overlapped the queue')
            self.assertEqual(job['worker_action'], 'install')
            self.assertEqual(job['worker_start'], 'fixture-start')
            self.assertEqual(self.service.get_job(parent['id'])['current_job_id'], job['id'])
            self.complete(job)

        self.service.run_install = Mock(side_effect=install)
        self.dispatch(parent)
        self.assertEqual(calls, [first['id'], second['id']])
        result = self.service.get_update_queue()
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(all(job['state'] == 'complete' for job in result['jobs']))
        self.dispatch(parent)
        self.assertEqual(calls, [first['id'], second['id']], 'Completed batches must not replay')
        with self.service.app_update_guard():
            pass

    def test_child_cannot_be_dispatched_outside_its_parent(self):
        child = self.ready()
        self.queue(child)
        self.service.run_install = Mock()
        with patch('eightbitdo.worker.Service', return_value=self.service):
            with self.assertRaisesRegex(ValueError, 'installation queue'):
                run(self.root, child['id'], 'install')
        self.service.run_install.assert_not_called()

    def test_failure_stops_queue_and_preserves_attempted_job_for_recovery(self):
        first, second, third = self.ready(), self.ready('usb-' + 'b' * 16), self.ready('usb-' + 'c' * 16)
        parent = self.queue(first, second, third)
        calls = []

        def install(job):
            calls.append(job['id'])
            if job['id'] == second['id']:
                job['original_version_raw'] = 101
                job['stage'] = 'writing'
                self.service.store.save(job)
                raise OSError('receiver disconnected')
            self.complete(job)

        self.service.run_install = Mock(side_effect=install)
        self.dispatch(parent)
        result = self.service.get_job(parent['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual([j['state'] for j in result['jobs']], ['complete', 'failed', 'cancelled'])
        self.assertEqual(calls, [first['id'], second['id']])
        child = self.service.store.read(second['id'])
        self.assertEqual(child['worker_action'], 'install')
        self.assertEqual(child['stage'], 'writing')
        self.assertEqual(child['original_version_raw'], 101)
        self.assertIn('receiver disconnected', child['message'])
        self.assertFalse(self.service.store.read(third['id'])['can_install'])

    def test_unverified_completion_stops_queue_without_recovery_or_next_write(self):
        first, second = self.ready(), self.ready('usb-' + 'b' * 16)
        parent = self.queue(first, second)

        def install(job):
            job.update(state='blocked', message='Version could not be verified.')
            self.service.store.save(job)

        self.service.run_install = Mock(side_effect=install)
        self.service.run_recover = Mock()
        self.dispatch(parent)
        self.assertEqual(self.service.run_install.call_count, 1)
        self.service.run_recover.assert_not_called()
        self.assertEqual(self.service.get_job(first['id'])['state'], 'blocked')
        self.assertEqual(self.service.get_job(second['id'])['state'], 'cancelled')

    def test_launch_failure_never_leaves_installable_or_replayable_claims(self):
        child = self.ready()
        self.host.start_worker.side_effect = OSError('launch failed')
        with self.assertRaises(RuntimeError):
            self.queue(child)
        parent = self.service.get_update_queue()
        self.assertEqual(parent['state'], 'failed')
        self.assertEqual(parent['jobs'][0]['state'], 'cancelled')
        self.assertFalse(parent['jobs'][0]['can_install'])
        with self.assertRaises(ValueError):
            self.service.install_update(child['id'])

    def test_dead_worker_is_reconciled_without_replaying_children(self):
        first, second = self.ready(), self.ready('usb-' + 'b' * 16)
        parent = self.queue(first, second)
        record = self.service.store.read(parent['id'])
        record.update(state='installing', current_job_id=first['id'], started_at=time.time() - 20)
        self.service.store.save(record)
        result = self.service.get_update_queue()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual([j['state'] for j in result['jobs']], ['failed', 'cancelled'])
        self.assertEqual(self.host.start_worker.call_count, 1)

    def test_waiting_child_follows_live_parent_instead_of_stale_worker_timeout(self):
        child = self.ready()
        parent = self.queue(child)
        record = self.service.store.read(child['id'])
        record['started_at'] = time.time() - 30
        self.service.store.save(record)
        self.assertEqual(self.service.get_job(child['id'])['state'], 'queued')
        self.assertEqual(self.service.get_job(parent['id'])['state'], 'queued')

    def test_running_queue_blocks_child_recovery(self):
        child = self.ready()
        self.queue(child)
        with self.assertRaisesRegex(ValueError, 'queue to stop'):
            self.service.check_recovery(child['id'])
        with self.assertRaisesRegex(ValueError, 'queue to stop'):
            self.service.recover_update(child['id'], True)

    def test_separate_recovery_can_run_after_queue_failure_without_being_cancelled(self):
        child = self.ready()
        parent = self.queue(child)
        self.service.run_install = Mock(side_effect=ValueError('Interrupted'))
        self.dispatch(parent)
        record = self.service.store.read(child['id'])
        record.update(state='queued', worker_action='recover', recovery_confirmed=True)
        self.service.store.save(record)

        def recover(job):
            status = self.service.get_update_queue()
            self.assertEqual(status['state'], 'failed')
            self.assertEqual(status['jobs'][0]['state'], 'queued')
            self.complete(job)

        self.service.run_recover = Mock(side_effect=recover)
        with patch('eightbitdo.worker.Service', return_value=self.service), \
                patch('eightbitdo.worker.process_start', return_value='fixture-recovery'):
            run(self.root, child['id'], 'recover')
        self.service.run_recover.assert_called_once()
        self.assertEqual(self.service.get_job(child['id'])['state'], 'complete')
        self.assertEqual(self.service.get_update_queue()['jobs'][0]['state'], 'complete')

    def test_changed_claim_stops_queue_before_any_install(self):
        child = self.ready()
        parent = self.queue(child)
        record = self.service.store.read(child['id'])
        record.update(state='blocked')
        self.service.store.save(record)
        self.service.run_install = Mock()
        self.dispatch(parent)
        self.assertEqual(self.service.get_update_queue()['state'], 'failed')
        self.service.run_install.assert_not_called()

    def test_parent_status_reconciles_a_stopped_recovery_worker_without_recursion(self):
        child = self.ready()
        parent = self.queue(child)
        self.service.run_install = Mock(side_effect=ValueError('Interrupted'))
        self.dispatch(parent)
        record = self.service.store.read(child['id'])
        record.update(state='queued', worker_action='recover', started_at=time.time() - 20,
                      recovery_confirmed=True)
        self.service.store.save(record)
        status = self.service.get_update_queue()
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['jobs'][0]['state'], 'failed')
        self.assertIn('worker stopped', status['jobs'][0]['message'])

    def test_pending_recovery_prevents_a_new_queue(self):
        child = self.ready()
        parent = self.queue(child)
        self.service.run_install = Mock(side_effect=ValueError('Interrupted'))
        self.dispatch(parent)
        record = self.service.store.read(child['id'])
        record.update(state='queued', worker_action='recover', started_at=time.time(),
                      recovery_confirmed=True)
        self.service.store.save(record)
        with self.assertRaisesRegex(ValueError, 'already running'):
            self.queue(self.ready('usb-' + 'b' * 16))

    def test_concurrent_status_reconciliation_does_not_clobber_a_locked_child(self):
        child = self.ready()
        parent = self.queue(child)
        record = self.service.store.read(parent['id'])
        record.update(state='failed', current_job_id=child['id'])
        self.service.store.save(record)
        with self.service.store.lock(child['id']):
            status = self.service.get_update_queue()
            self.assertEqual(status['jobs'][0]['state'], 'queued')
        status = self.service.get_update_queue()
        self.assertEqual(status['jobs'][0]['state'], 'failed')

    def test_slow_admission_keeps_parent_locked_against_dead_worker_reconciliation(self):
        child = self.ready()
        save = self.service.store.save
        checked = False

        def slow_save(job):
            nonlocal checked
            save(job)
            if job.get('batch_id') and not checked:
                checked = True
                parent = self.service.store.read(job['batch_id'])
                parent['started_at'] = time.time() - 30
                save(parent)
                with self.assertRaises(RuntimeError):
                    with self.service.store.lock(parent['id']):
                        self.fail('Queue admission did not retain its parent lock')
                self.assertEqual(self.service.get_update_queue()['state'], 'queued')

        with patch.object(self.service.store, 'save', side_effect=slow_save):
            parent = self.queue(child)
        self.assertTrue(checked)
        self.assertEqual(self.service.get_update_queue()['state'], 'queued')
        self.assertEqual(self.host.start_worker.call_count, 1)
        self.assertEqual(parent['state'], 'queued')


if __name__ == '__main__':
    unittest.main()
