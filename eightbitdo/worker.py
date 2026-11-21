import os
import sys
from pathlib import Path

from .discovery import clean_text
from .service import Service


def run(state_dir: Path, job_id: str, action: str) -> None:
    if action not in {"prepare", "install"}:
        raise ValueError("Unknown worker action.")
    service = Service(state_dir)
    with service.store.lock(job_id):
        job = service.store.read(job_id)
        if job["state"] != "queued":
            return
        job["worker_pid"] = os.getpid()
        job["worker_start"] = Path("/proc/self/stat").read_text().split()[21]
        service.store.save(job)
        try:
            if action == "prepare":
                service.run_prepare(job)
            else:
                service.run_install(job)
        except Exception as error:
            job.update(state="failed", can_install=False, message=clean_text(str(error), 1200) or "The update worker failed.")
            service.store.save(job)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Expected state directory, job ID, and action.")
    run(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
