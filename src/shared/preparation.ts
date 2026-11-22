import type { Device, Model } from "./types";

export function preparationAdvice(model: Model | undefined, device: Device | undefined, nativeWrite = true) {
  if (!nativeWrite) {
    return {
      label: "Download firmware only",
      description: "Firmware installation is unavailable on this platform. Downloading leaves your device unchanged.",
    };
  }
  if (model?.download_only) {
    return {
      label: "Download firmware only",
      description: "This app does not install firmware on this model yet. Downloading checks the firmware file and leaves your device unchanged.",
    };
  }
  if (!device || device.model_id !== model?.id || !["identified", "supported"].includes(device.status)) {
    return {
      label: "Download firmware only",
      description: "Select an identified connected device to check installation eligibility. Downloading leaves your device unchanged.",
    };
  }
  return {
    label: "Prepare firmware",
    description: "Downloads firmware and checks whether this device and version can be updated. Installation requires a separate confirmation.",
  };
}
