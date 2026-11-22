import json
import os
import re
import tempfile
import time
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from .locking import exclusive_lock


ACTIVE_STATES = {"queued", "downloading", "validating", "installing", "verifying"}


def default_state_dir() -> Path:
    if sys.platform in {"darwin", "win32"}:
        from platformdirs import user_state_path
        return user_state_path("8BitDo Updater", appauthor=False)
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
        self._write_json(self.path(job["id"]), job)

    @staticmethod
    def _write_json(destination: Path, value) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".job-", dir=destination.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            # Windows readers/virus scanners can briefly hold a handle without
            # delete sharing. Retain atomic replacement, with a bounded retry.
            deadline = time.monotonic() + 1.0 if sys.platform == "win32" else 0
            while True:
                try:
                    os.replace(temporary, destination)
                    break
                except PermissionError:
                    if sys.platform != "win32" or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def registered_states(self) -> set[Path]:
        path = self.root / 'firmware-state-dirs.json'
        if not path.exists():
            return set()
        value = json.loads(path.read_text())
        if not isinstance(value, list) or any(not isinstance(item, str) or not Path(item).is_absolute() for item in value):
            raise ValueError('Invalid firmware state directory registry.')
        return {Path(item) for item in value}

    def register_state(self, state_dir: Path) -> None:
        states = self.registered_states()
        state_dir = state_dir.resolve()
        if state_dir not in states:
            self.write_registered_states(states | {state_dir})

    def write_registered_states(self, states: set[Path]) -> None:
        self._write_json(self.root / 'firmware-state-dirs.json', sorted(str(path) for path in states))

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
        with exclusive_lock(directory / (key + ".lock")):
            yield


def public_job(job: dict) -> dict:
    return {key: job[key] for key in (
        "id", "state", "progress", "message", "can_install", "target_label",
        "evidence_level", "evidence",
        "can_check_recovery", "recovery_ready", "recovery_action",
    ) if key in job}
