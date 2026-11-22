import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eightbitdo.api import UpdaterAPI


class Plugin:
    def __init__(self):
        self.api = UpdaterAPI()

    @property
    def service(self):
        # Compatibility for existing package smoke checks and embedded callers.
        return self.api.service

    async def _main(self):
        pass

    async def _unload(self):
        # Update jobs run in a separate session and survive sidebar/plugin unloads.
        pass

    async def _call(self, operation: str, **parameters):
        return await asyncio.to_thread(self.api.call, operation, **parameters)

    async def scan_devices(self):
        return await self._call("scan_devices")

    async def get_models(self):
        return await self._call("get_models")

    async def get_recovery_access(self):
        return await self._call("get_recovery_access")

    async def setup_recovery_access(self, confirmed: bool = False):
        return await self._call("setup_recovery_access", confirmed=confirmed)

    async def get_releases(self, model_id: str):
        return await self._call("get_releases", model_id=model_id)

    async def prepare_update(self, device_id: str, model_id: str, release_id: str):
        return await self._call("prepare_update", device_id=device_id, model_id=model_id, release_id=release_id)

    async def get_job(self, job_id: str):
        return await self._call("get_job", job_id=job_id)

    async def install_update(self, job_id: str, confirmed: bool = False):
        return await self._call("install_update", job_id=job_id, confirmed=confirmed)

    async def check_recovery(self, job_id: str):
        return await self._call("check_recovery", job_id=job_id)

    async def recover_update(self, job_id: str, confirmed: bool = False):
        return await self._call("recover_update", job_id=job_id, confirmed=confirmed)

    async def export_diagnostics(self):
        return await self._call("export_diagnostics")

    async def open_official_updater(self):
        return await self._call("open_official_updater")
