import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/decky/firmware.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { automaticDevice, chooseLatest, compareVersions, deviceKey } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

const modelId = "ultimate2-controller";
const release = (id, version, downloadable = true) => ({ id, version, downloadable, date: "", notes: "" });

test("latest selection sorts native hundredths, independent of vendor order and date", () => {
  const latest = release("new", "1.10");
  const old = { ...release("old", "1.09"), date: "2099-01-01" };
  assert.equal(chooseLatest([old, latest, release("older", "1.02")], modelId), latest);
  assert.equal(compareVersions("1.1", "1.10", modelId), 0);
  assert.equal(compareVersions("1.1", "1.09", modelId), 1);
  assert.equal(compareVersions("1.10", "2.00", modelId), -1);
  assert.equal(compareVersions("655.35", "655.34", modelId), 1);
  assert.equal(compareVersions("655.36", "1.10", modelId), undefined);
});

test("fwupd uses numeric pair versions rather than native hundredths", () => {
  const fwupd = "fwupd-device";
  const latest = release("new", "1.10");
  assert.equal(compareVersions("1.1", "1.10", fwupd), -1);
  assert.equal(compareVersions("1.01", "1.1", fwupd), 0);
  assert.equal(chooseLatest([latest, release("old", "1.9")], fwupd), latest);
  assert.equal(compareVersions("100000.1", "1.10", fwupd), undefined);
});

test("installed version comparisons distinguish current, newer, and unknown firmware", () => {
  assert.equal(compareVersions("1.10", "1.1", modelId), 0);
  assert.equal(compareVersions("1.11", "1.10", modelId), 1);
  assert.equal(compareVersions("1.09", "1.10", modelId), -1);
  for (const version of ["1.10 beta", "1.10b1", "1.10.1", "v1.10", "", "unknown", "1e2.00", "1.10 "]) {
    assert.equal(compareVersions(version, "1.10", modelId), undefined, version);
    assert.equal(chooseLatest([release("known", "1.10"), release("unknown", version)], modelId), undefined, version);
  }
});

test("ambiguous latest images are not selected automatically", () => {
  assert.equal(chooseLatest([], modelId), undefined);
  assert.equal(chooseLatest([release("a", "1.10"), release("b", "1.1")], modelId), undefined);
  const latest = release("latest", "1.11");
  assert.equal(chooseLatest([release("a", "1.10"), release("b", "1.1"), latest], modelId), latest);
  assert.equal(chooseLatest([latest, release("a", "1.10"), release("b", "1.1")], modelId), latest);
});

test("an unavailable latest release does not silently select an older download", () => {
  const latest = release("latest", "1.11", false);
  assert.equal(chooseLatest([release("old", "1.10"), latest], modelId), latest);
});

test("automatic binding requires identified hardware, not a manual model guess or can_update flag", () => {
  const device = { id: "usb-1", model_id: modelId, status: "identified", can_update: false };
  const models = [{ id: modelId }];
  assert.equal(automaticDevice(device, models), true);
  assert.equal(automaticDevice({ ...device, status: "supported" }, models), true);
  for (const status of ["detected", "ambiguous", "unknown", "wireless", "unavailable", "connection_required"]) {
    assert.equal(automaticDevice({ ...device, status, can_update: true }, models), false, status);
  }
  assert.equal(automaticDevice({ ...device, model_id: null }, models), false);
  assert.equal(automaticDevice(device, []), false);
});

test("prepared selection keys change with target, transport, identity, or firmware", () => {
  const device = {
    id: "usb-1", model_id: modelId, vid: "2dc8", pid: "6012", role: "controller",
    backend: "hid", status: "identified", firmware_version: "1.09", name: "Controller",
  };
  const key = deviceKey(device);
  for (const [field, value] of Object.entries({
    id: "usb-2", model_id: "ultimate2-receiver", vid: "0000", pid: "6013", role: "receiver",
    backend: "wireless", status: "unavailable", firmware_version: "1.10",
  })) assert.notEqual(deviceKey({ ...device, [field]: value }), key, field);
  assert.equal(deviceKey({ ...device, name: "Friendly name" }), key);
});
