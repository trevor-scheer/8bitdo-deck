import type { Device, Model, Release } from "../shared/types";

export function deviceKey(device: Device): string {
  return JSON.stringify([
    device.id, device.model_id, device.vid, device.pid, device.role,
    device.backend, device.status, device.firmware_version,
  ]);
}

function versionParts(version: string, modelId: string): number[] | undefined {
  if (modelId.startsWith("fwupd-")) {
    if (!/^[0-9]{1,5}\.[0-9]{1,5}$/.test(version)) return undefined;
    return version.split(".").map(Number);
  }
  const match = /^([0-9]{1,3})\.([0-9]{1,2})$/.exec(version);
  if (!match) return undefined;
  // Native firmware headers encode hundredths, so vendor 1.1 means 1.10.
  const value = Number(match[1]) * 100 + Number(match[2].padEnd(2, "0"));
  return value <= 0xFFFF ? [value] : undefined;
}

export function compareVersions(a: string, b: string, modelId: string): number | undefined {
  const left = versionParts(a, modelId);
  const right = versionParts(b, modelId);
  if (!left || !right) return undefined;
  for (let index = 0; index < left.length; index++) {
    if (left[index] !== right[index]) return Math.sign(left[index] - right[index]);
  }
  return 0;
}

export function chooseLatest(releases: Release[], modelId: string): Release | undefined {
  let latest: Release | undefined;
  let ambiguous = false;
  for (const release of releases) {
    const comparison = compareVersions(release.version, latest?.version ?? release.version, modelId);
    // Catalog APIs already exclude beta releases. An unexpected version makes
    // automatic ordering uncertain; leave selection to the manual browser.
    if (comparison === undefined) return undefined;
    if (!latest || comparison > 0) {
      latest = release;
      ambiguous = false;
    } else if (comparison === 0 && release.id !== latest.id) {
      ambiguous = true;
    }
  }
  return ambiguous ? undefined : latest;
}

export function automaticDevice(device: Device, models: Model[]): boolean {
  return ["identified", "supported"].includes(device.status)
    && models.some((model) => model.id === device.model_id);
}
