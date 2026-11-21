import fcntl
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


ACTIVE_STATES = {"queued", "downloading", "validating", "installing", "verifying"}


def default_state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "eightbitdo-deck"


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid update job ID.")
        return self.directory / (job_id + ".json")

    def create(self, device_id: str, model_id: str, release_id: str) -> dict:
        job = {
            "id": uuid.uuid4().hex, "state": "queued", "progress": 0,
            "message": "Preparing the selected firmware.", "can_install": False,
            "device_id": device_id, "model_id": model_id, "release_id": release_id,
            "created_at": time.time(), "worker_pid": None,
        }
        self.save(job)
        return job

    def save(self, job: dict) -> None:
        destination = self.path(job["id"])
        fd, temporary = tempfile.mkstemp(prefix=".job-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(job, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def read(self, job_id: str) -> dict:
        try:
            value = json.loads(self.path(job_id).read_text())
        except FileNotFoundError:
            raise ValueError("This update job was not found.") from None
        if not isinstance(value, dict) or value.get("id") != job_id:
            raise ValueError("Invalid update job record.")
        return value

    @contextmanager
    def lock(self, key: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError("Invalid lock key.")
        directory = self.root / "locks"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(directory / (key + ".lock"), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another operation is already using this device or job.") from None
            yield
        finally:
            os.close(fd)


def public_job(job: dict) -> dict:
    return {key: job[key] for key in (
        "id", "state", "progress", "message", "can_install", "target_label",
        "evidence_level", "evidence",
    ) if key in job}
