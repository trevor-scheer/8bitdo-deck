import os
import sys
from pathlib import Path

from .discovery import clean_text
from .service import Service
from .platform import process_start


def run(state_dir: Path, job_id: str, action: str) -> None:
    if action not in {"prepare", "install", "recover", "batch"}:
        raise ValueError("Unknown worker action.")
    service = Service(state_dir)
    with service.worker_guard(), service.store.lock(job_id):
        job = service.store.read(job_id)
        if job["state"] != "queued":
            return
        if job.get("worker_action", action) != action:
            raise ValueError("Worker action does not match the queued job.")
        if job.get("batch_id"):
            if action != "recover":
                raise ValueError("A queued update can only be started by its installation queue.")
            from .batch import require_batch_finished
            require_batch_finished(service, job)
        job["worker_pid"] = os.getpid()
        job["worker_start"] = process_start(os.getpid())
        service.store.save(job)
        try:
            if action == "prepare":
                service.run_prepare(job)
            elif action == "install":
                service.run_install(job)
            elif action == "batch":
                service.run_batch(job)
            else:
                service.run_recover(job)
        except Exception as error:
            job.update(state="failed", can_install=False, recovery_ready=False, recovery_confirmed=False,
                       message=clean_text(str(error), 1200) or "The update worker failed.")
            service.store.save(job)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Expected state directory, job ID, and action.")
    run(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
