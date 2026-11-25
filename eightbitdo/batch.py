"""Durable, sequential installation of explicitly selected prepared jobs."""

from contextlib import ExitStack
import json
import time

from .discovery import clean_text
from .jobs import ACTIVE_STATES, public_job


MAX_BATCH_SIZE = 32


def get_update_queue(service):
    path = service.state_dir / "latest-update-queue.json"
    try:
        queue_id = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    service.store.path(queue_id)
    job = service.store.read(queue_id)
    if job.get("backend") != "batch":
        raise ValueError("The recorded update queue is invalid.")
    return service.get_job(queue_id)


def require_installable(service, job):
    if (job["state"] != "ready" or job.get("can_install") is not True
            or job.get("backend") not in {"fwupd", "hid", "wireless"}
            or job.get("batch_id")):
        raise ValueError("This firmware is not qualified for installation. No firmware was written.")
    if job.get("backend") in {"hid", "wireless"} and not service.recovery_access.status()["configured"]:
        raise ValueError("Configure USB recovery access before installing firmware.")


def install_updates(service, job_ids):
    if type(job_ids) is not list or not 1 <= len(job_ids) <= MAX_BATCH_SIZE:
        raise ValueError(f"Select between 1 and {MAX_BATCH_SIZE} prepared updates.")
    for job_id in job_ids:
        service.store.path(job_id)
    if len(set(job_ids)) != len(job_ids):
        raise ValueError("An update cannot appear twice in the same queue.")
    service.platform.require_write_support()
    with service._queue_guard(), ExitStack() as locks:
        previous = get_update_queue(service)
        if previous and (previous["state"] in ACTIVE_STATES
                         or any(child["state"] in ACTIVE_STATES for child in previous["jobs"])):
            raise ValueError("An update queue is already running. Wait for it to finish.")
        for job_id in sorted(job_ids):
            locks.enter_context(service.store.lock(job_id))
        jobs = [service.store.read(job_id) for job_id in job_ids]
        targets = set()
        for job in jobs:
            require_installable(service, job)
            target = job.get("device_id", "")
            if job["backend"] == "wireless":
                from .wireless_service import receiver_id
                target = receiver_id(target)
            if not target or target in targets:
                raise ValueError("Select only one update per physical device or receiver; update its receiver first, then scan again.")
            targets.add(target)
        parent = service.store.create("", "", "")
        locks.enter_context(service.store.lock(parent["id"]))
        parent.update(backend="batch", job_ids=job_ids, target_label=f"{len(jobs)} device update{'s' if len(jobs) != 1 else ''}",
                      message="Starting updates one device at a time. Keep devices connected.")
        service.store.save(parent)
        try:
            service.store._write_json(service.state_dir / "latest-update-queue.json", parent["id"])
            for job in jobs:
                job.update(state="queued", can_install=False, progress=0, batch_id=parent["id"],
                           worker_action="install", worker_pid=None, started_at=time.time(),
                           message="Waiting for its turn in the update queue.")
                service.store.save(job)
            service._spawn(parent, "batch")
        except Exception:
            parent.update(state="failed", message="Could not start the update queue. No queued update was started.")
            service.store.save(parent)
            for job in jobs:
                if job.get("batch_id") == parent["id"]:
                    job.update(state="cancelled", can_install=False,
                               message="The update queue could not start. Scan and prepare again.")
                    service.store.save(job)
            raise
        result = public_job(parent)
        result["jobs"] = [public_job(job) for job in jobs]
        return result


def stop_unfinished(service, parent):
    """Never replay a claimed child after a failed, stopped, or interrupted queue."""
    for job_id in parent["job_ids"]:
        snapshot = service.store.read(job_id)
        if snapshot.get("worker_action") != "install" or snapshot["state"] not in ACTIVE_STATES:
            continue
        try:
            with service.store.lock(job_id):
                job = service.store.read(job_id)
                if (job.get("batch_id") != parent["id"] or job["state"] not in ACTIVE_STATES
                        or job.get("worker_action") != "install"):
                    continue
                attempted = job_id == parent.get("current_job_id")
                job.update(state="failed" if attempted else "cancelled", can_install=False,
                           recovery_ready=False, recovery_confirmed=False,
                           message=("The update queue stopped. Check this interrupted update before retrying."
                                    if attempted else "Not started because the update queue stopped. Scan and prepare again."))
                service.store.save(job)
        except RuntimeError:
            # A concurrent status/recovery operation owns this child. A later
            # status read will reconcile it without racing that operation.
            continue


def run_batch(service, parent):
    if parent.get("backend") != "batch" or parent.get("worker_action") != "batch":
        raise ValueError("This job is not an installation queue.")
    service.platform.require_write_support()
    parent.update(state="installing", progress=0)
    service.store.save(parent)
    try:
        for index, job_id in enumerate(parent["job_ids"]):
            with service.store.lock(job_id):
                job = service.store.read(job_id)
                if (job.get("batch_id") != parent["id"] or job["state"] != "queued"
                        or job.get("worker_action") != "install"):
                    raise ValueError("A queued update changed. Scan and prepare again.")
                parent.update(current_job_id=job_id, progress=int(index * 100 / len(parent["job_ids"])),
                              message=f"Updating device {index + 1} of {len(parent['job_ids'])}. Keep devices connected.")
                service.store.save(parent)
                job.update(worker_pid=parent.get("worker_pid"), worker_start=parent.get("worker_start"),
                           started_at=time.time())
                service.store.save(job)
                try:
                    # The existing backend rechecks identity, cached bytes, source
                    # firmware and authorization immediately before device writes.
                    service.run_install(job)
                except Exception as error:
                    job.update(state="failed", can_install=False, recovery_ready=False, recovery_confirmed=False,
                               message=clean_text(str(error), 1200) or "The update failed.")
                    service.store.save(job)
                if job["state"] != "complete":
                    raise RuntimeError("Update queue stopped: " + job["message"])
        parent.update(state="complete", progress=100, message="All selected updates completed and verified.")
    except Exception as error:
        parent.update(state="failed", can_install=False,
                      message=clean_text(str(error), 1200) or "The update queue stopped.")
    finally:
        service.store.save(parent)
        if parent["state"] != "complete":
            stop_unfinished(service, parent)


def require_batch_finished(service, job):
    if job.get("batch_id") and service.get_job(job["batch_id"])["state"] in ACTIVE_STATES:
        raise ValueError("Wait for the update queue to stop before checking or recovering this device.")
