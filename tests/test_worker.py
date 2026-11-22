import tempfile
from contextlib import nullcontext
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from eightbitdo.jobs import JobStore
from eightbitdo.service import Service
from eightbitdo.worker import run


class RecoveryWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = JobStore(self.root)
        self.job = self.store.create('usb-fixture', 'ultimate2-receiver', 'release')
        self.job.update(worker_action='recover', recovery_confirmed=True, recovery_ready=False)
        self.store.save(self.job)
        self.service = Mock(store=self.store)
        self.service.worker_guard = nullcontext

    def test_recovery_dispatch_holds_job_lock_and_does_not_install(self):
        def recover(job):
            with self.assertRaises(RuntimeError):
                with self.store.lock(job['id']):
                    self.fail('Worker released the job lock')
            self.assertTrue(job['recovery_confirmed'])
        self.service.run_recover.side_effect = recover
        with patch('eightbitdo.worker.Service', return_value=self.service):
            run(self.root, self.job['id'], 'recover')
        self.service.run_recover.assert_called_once()
        self.service.run_install.assert_not_called()
        self.service.run_prepare.assert_not_called()

    def test_recovery_failure_clears_confirmation_and_reset_readiness(self):
        self.service.run_recover.side_effect = ValueError('Source authorization changed')
        with patch('eightbitdo.worker.Service', return_value=self.service):
            run(self.root, self.job['id'], 'recover')
        job = self.store.read(self.job['id'])
        self.assertEqual(job['state'], 'failed')
        self.assertFalse(job['can_install'])
        self.assertFalse(job['recovery_ready'])
        self.assertFalse(job['recovery_confirmed'])
        self.assertEqual(job['message'], 'Source authorization changed')

    def test_worker_action_mismatch_is_rejected_before_dispatch(self):
        with patch('eightbitdo.worker.Service', return_value=self.service):
            with self.assertRaisesRegex(ValueError, 'action'):
                run(self.root, self.job['id'], 'install')
        self.service.run_install.assert_not_called()
        self.service.run_recover.assert_not_called()

    def test_real_worker_guard_blocks_application_replacement_through_dispatch(self):
        host = Mock()
        host.worker_running.return_value = False
        service = Service(self.root, coordination_dir=self.root, platform=host)

        def recover(job):
            with self.assertRaises(RuntimeError):
                with service.app_update_guard():
                    self.fail('Application replacement overlapped a firmware worker')
            job['state'] = 'complete'
            service.store.save(job)

        service.run_recover = Mock(side_effect=recover)
        with patch('eightbitdo.worker.Service', return_value=service):
            run(self.root, self.job['id'], 'recover')
        service.run_recover.assert_called_once()
        with service.app_update_guard():
            pass


if __name__ == '__main__':
    unittest.main()
