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
  if (model?.wireless_only && device?.backend !== "wireless") {
    return {
      label: "Download firmware only",
      description: "For installation, connect the controller in 2.4 GHz mode through its USB receiver and select the controller listed via receiver. Bluetooth and direct USB installation are not enabled.",
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
    description: device.backend === "wireless"
      ? "Checks the controller and receiver before a 2.4 GHz update. Keep the receiver connected by USB and charge the controller to at least 30%. Installation requires a separate confirmation."
      : "Downloads firmware and checks whether this device and version can be updated. Installation requires a separate confirmation.",
  };
}
