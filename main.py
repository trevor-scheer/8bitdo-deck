import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eightbitdo.service import Service


class Plugin:
    def __init__(self):
        self.service = Service()

    async def _main(self):
        pass

    async def _unload(self):
        # Update jobs run in a separate session and survive sidebar/plugin unloads.
        pass

    async def scan_devices(self):
        return await asyncio.to_thread(self.service.scan_devices)

    async def get_models(self):
        return await asyncio.to_thread(self.service.get_models)

    async def get_recovery_access(self):
        return await asyncio.to_thread(self.service.get_recovery_access)

    async def setup_recovery_access(self):
        return await asyncio.to_thread(self.service.setup_recovery_access)

    async def get_releases(self, model_id: str):
        return await asyncio.to_thread(self.service.get_releases, model_id)

    async def prepare_update(self, device_id: str, model_id: str, release_id: str):
        return await asyncio.to_thread(self.service.prepare_update, device_id, model_id, release_id)

    async def get_job(self, job_id: str):
        return await asyncio.to_thread(self.service.get_job, job_id)

    async def install_update(self, job_id: str):
        return await asyncio.to_thread(self.service.install_update, job_id)

    async def export_diagnostics(self):
        return await asyncio.to_thread(self.service.export_diagnostics)

    async def open_official_updater(self):
        return await asyncio.to_thread(self.service.open_official_updater)
