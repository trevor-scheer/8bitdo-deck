import { invoke, isTauri } from "@tauri-apps/api/core";
import type { UpdaterClient } from "../src/shared/types";

type Envelope<T> =
  | { api_version: number; ok: true; result: T }
  | {
      api_version: number;
      ok: false;
      error: { code: string; message: string };
    };

export const desktopAPI: UpdaterClient = {
  async request<T>(
    operation: string,
    parameters: Record<string, unknown> = {},
  ): Promise<T> {
    if (!isTauri())
      throw new Error(
        "Open the desktop application to connect to the updater. Browser preview cannot access your devices.",
      );
    const response = await invoke<Envelope<T>>("updater_request", {
      operation,
      parameters,
    });
    if (response.api_version !== 1)
      throw new Error(
        "This application and its updater use incompatible API versions.",
      );
    if (!response.ok) throw new Error(response.error.message);
    return response.result;
  },
};

export async function quitDesktop(): Promise<void> {
  if (!isTauri()) throw new Error("Close this browser preview manually.");
  await invoke("quit_application");
}
