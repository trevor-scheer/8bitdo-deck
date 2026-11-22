import { callable } from "@decky/api";

import type { Device, Job, Model, RecoveryAccess, Release } from "./shared/types";
export type { Device, Job, Model, RecoveryAccess, Release } from "./shared/types";

export const scanDevices = callable<[], { devices: Device[]; warnings: string[] }>("scan_devices");
export const getModels = callable<[], Model[]>("get_models");
export const getReleases = callable<[modelId: string], { releases: Release[]; error?: string }>("get_releases");
export const prepareUpdate = callable<[
  deviceId: string,
  modelId: string,
  releaseId: string,
], { job_id?: string; error?: string }>("prepare_update");
export const getJob = callable<[jobId: string], Job>("get_job");
export const installUpdate = callable<[jobId: string, confirmed: boolean], Job>("install_update");
export const checkRecovery = callable<[jobId: string], Job>("check_recovery");
export const recoverUpdate = callable<[jobId: string, confirmed: boolean], Job>("recover_update");
export const exportDiagnostics = callable<[], { path?: string; error?: string }>("export_diagnostics");
export const openOfficialUpdater = callable<[], { opened: boolean; error?: string }>("open_official_updater");
export const getRecoveryAccess = callable<[], RecoveryAccess>("get_recovery_access");
export const setupRecoveryAccess = callable<[confirmed: boolean], RecoveryAccess>("setup_recovery_access");
