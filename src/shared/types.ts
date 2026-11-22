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
  can_check_recovery?: boolean;
  recovery_ready?: boolean;
  recovery_action?: string;
}

export interface RecoveryAccess {
  configured: boolean;
  pending: boolean;
  can_setup: boolean;
  message: string;
}

export interface AppInfo {
  app_version: string;
  api_version: number;
  platform: string;
  platform_supported: boolean;
  capabilities?: Record<string, boolean>;
  operations: Record<string, unknown>;
}

export interface AppUpdate {
  current_version: string;
  channel: string;
  platform: string;
  arch: string;
  available_version: string | null;
  release_notes: string;
  state: string;
  can_apply: boolean;
  can_recover?: boolean;
  restart_required: boolean;
  message: string;
}

export interface UpdaterClient {
  request<T>(
    operation: string,
    parameters?: Record<string, unknown>,
  ): Promise<T>;
}
