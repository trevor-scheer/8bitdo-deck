import { callable } from "@decky/api";

export interface Device {
  id: string;
  name: string;
  vid: string;
  pid: string;
  role: string;
  model_id: string | null;
  firmware_version: string | null;
  status: string;
  can_update: boolean;
  reason: string;
}

export interface Model {
  id: string;
  name: string;
  role: string;
  instructions: string;
  support: string;
}

export interface Release {
  id: string;
  version: string;
  date: string;
  notes: string;
  downloadable: boolean;
}

export interface Job {
  id: string;
  state: string;
  progress: number;
  message: string;
  can_install: boolean;
  target_label?: string;
  evidence_level?: string;
  evidence?: string;
}

export interface RecoveryAccess {
  configured: boolean;
  pending: boolean;
  can_setup: boolean;
  message: string;
}

export const scanDevices = callable<[], { devices: Device[]; warnings: string[] }>("scan_devices");
export const getModels = callable<[], Model[]>("get_models");
export const getReleases = callable<[modelId: string], { releases: Release[]; error?: string }>("get_releases");
export const prepareUpdate = callable<[
  deviceId: string,
  modelId: string,
  releaseId: string,
], { job_id?: string; error?: string }>("prepare_update");
export const getJob = callable<[jobId: string], Job>("get_job");
export const installUpdate = callable<[jobId: string], Job>("install_update");
export const exportDiagnostics = callable<[], { path?: string; error?: string }>("export_diagnostics");
export const openOfficialUpdater = callable<[], { opened: boolean; error?: string }>("open_official_updater");
export const getRecoveryAccess = callable<[], RecoveryAccess>("get_recovery_access");
export const setupRecoveryAccess = callable<[], RecoveryAccess>("setup_recovery_access");
