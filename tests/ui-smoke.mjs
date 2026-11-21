import assert from "node:assert/strict";
import test from "node:test";
import * as React from "react";
import * as JSX from "react/jsx-runtime";
import TestRenderer, { act } from "react-test-renderer";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.SP_REACT = React;
globalThis.SP_JSX = JSX;
globalThis.DFL = Object.fromEntries(
  ["ButtonItem", "DropdownItem", "PanelSection", "PanelSectionRow"].map((name) => [
    name, (props) => React.createElement(name, props),
  ]),
);
globalThis.DFL.staticClasses = { Title: "title" };

let handlers;
globalThis.window = {
  __DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit: {
    connect: () => ({
      _version: 2,
      callable: (name) => (...args) => handlers[name](...args),
    }),
  },
};

const { default: plugin } = await import("../dist/index.js");

async function settle(action) {
  await act(async () => {
    await action?.();
    for (let step = 0; step < 6; step++) await Promise.resolve();
  });
}

async function mount(t) {
  const storage = new Map();
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  const receiverModel = {
    id: "ultimate2-receiver", name: "Ultimate 2 Wireless", role: "receiver",
    instructions: "Connect receiver by USB.", support: "Native support requires validation.",
  };
  const controllerModel = { ...receiverModel, id: "ultimate2-controller", role: "controller" };
  const unknown = {
    id: "ambiguous", name: "Ultimate Wireless", vid: "2dc8", pid: "3109", role: "unknown",
    model_id: null, firmware_version: null, status: "unknown", can_update: false,
    reason: "Identify exact model first.",
  };
  const receiver = {
    id: "receiver", name: "Ultimate 2 Wireless receiver", vid: "2dc8", pid: "6013", role: "receiver",
    model_id: receiverModel.id, firmware_version: "1.04", status: "identified", can_update: false,
    reason: "Native flash unverified.",
  };
  const state = {
    devices: [unknown, receiver], scanOverride: null,
    releaseModels: [], prepareCalls: [], installCalls: [], jobs: new Map(), nativeReady: false,
    evidenceLevel: undefined, recoveryChecks: 0, recoverySetupCalls: 0, recoveryError: null,
    recoveryAccess: { configured: false, pending: false, can_setup: true, message: "Recovery access needs setup." },
  };
  handlers = {
    scan_devices: async () => state.scanOverride ?? { devices: state.devices, warnings: [] },
    get_models: async () => [receiverModel, controllerModel],
    get_releases: async (id) => {
      state.releaseModels.push(id);
      return { releases: [{ id: "r1", version: "1.05", date: "", notes: "Fixes reconnecting.", downloadable: true }] };
    },
    prepare_update: async (...args) => {
      state.prepareCalls.push(args);
      const id = `job-${state.prepareCalls.length}`;
      state.jobs.set(id, {
        id, state: state.nativeReady ? "ready" : "blocked", progress: 100,
        message: state.nativeReady ? "Ready to install." : "Native flash unverified.",
        can_install: state.nativeReady, target_label: "Ultimate 2 receiver firmware 1.05",
        evidence_level: state.evidenceLevel,
      });
      return { job_id: id };
    },
    get_job: async (id) => state.jobs.get(id),
    install_update: async (id) => {
      state.installCalls.push(id);
      const job = { ...state.jobs.get(id), state: "complete", progress: 100, message: "Verified.", can_install: false };
      state.jobs.set(id, job);
      return job;
    },
    export_diagnostics: async () => ({ path: "/tmp/diagnostics.json" }),
    open_official_updater: async () => ({ opened: true }),
    get_recovery_access: async () => {
      state.recoveryChecks++;
      if (state.recoveryError) throw state.recoveryError;
      return state.recoveryAccess;
    },
    setup_recovery_access: async () => {
      state.recoverySetupCalls++;
      state.recoveryAccess = { ...state.recoveryAccess, pending: true, message: "Approve the SteamOS administrator prompt." };
      return state.recoveryAccess;
    },
  };
  let renderer;
  await settle(() => { renderer = TestRenderer.create(plugin().content); });
  t.after(async () => { await settle(() => renderer.unmount()); });
  const button = (label) => renderer.root.findAllByType("ButtonItem").find((item) => item.props.children === label);
  const modelPicker = () => renderer.root.findAllByType("DropdownItem")[0];
  const click = async (label) => {
    const item = button(label);
    assert.ok(item, `Missing button: ${label}`);
    assert.equal(Boolean(item.props.disabled), false, `Button is disabled: ${label}`);
    await settle(() => item.props.onClick());
  };
  const renderedText = () => JSON.stringify(renderer.toJSON());
  return { state, receiverModel, controllerModel, unknown, receiver, button, modelPicker, click, renderedText };
}

test("identified devices automatically select and load firmware, while rescans reconcile identity", async (t) => {
  const { state, receiverModel, controllerModel, unknown, receiver, button, modelPicker, click } = await mount(t);
  assert.ok(button(unknown.name));
  assert.ok(button(receiver.name));
  assert.equal(modelPicker().props.selectedOption, receiverModel.id);
  assert.ok(state.releaseModels.includes(receiverModel.id));
  assert.ok(button(receiver.name).props.description.includes("Selected"));

  await click("Scan connected devices");
  assert.equal(modelPicker().props.selectedOption, receiverModel.id);

  state.devices = [{ ...receiver, model_id: controllerModel.id, role: "controller" }];
  await click("Scan connected devices");
  assert.equal(modelPicker().props.selectedOption, controllerModel.id);
  assert.ok(state.releaseModels.includes(controllerModel.id));

  state.devices = [];
  await click("Scan connected devices");
  assert.equal(modelPicker().props.selectedOption, "");
  assert.equal(button("Prepare firmware"), undefined);

  state.devices = [{ ...receiver, status: "candidate" }, { ...unknown, model_id: controllerModel.id }];
  await click("Scan connected devices");
  assert.equal(modelPicker().props.selectedOption, "", "Unverified candidates must not select a firmware target");
});

test("a pending scan cannot replace a newer manual model choice", async (t) => {
  const { state, controllerModel, modelPicker, click } = await mount(t);
  let finishScan;
  state.scanOverride = new Promise((resolve) => { finishScan = resolve; });
  await click("Scan connected devices");
  await settle(() => modelPicker().props.onChange({ data: controllerModel.id }));
  await settle(() => finishScan({ devices: state.devices, warnings: [] }));
  state.scanOverride = null;
  assert.equal(modelPicker().props.selectedOption, controllerModel.id);

  await click("Scan connected devices");
  assert.equal(modelPicker().props.selectedOption, controllerModel.id, "Manual browsing must survive later rescans");
});

test("manual browsing does not identify hardware or enable an unsupported write", async (t) => {
  const { state, receiverModel, unknown, modelPicker, button, click } = await mount(t);
  await click(unknown.name);
  assert.equal(button("Prepare firmware"), undefined);
  await settle(() => modelPicker().props.onChange({ data: receiverModel.id }));
  await click("Prepare firmware");
  assert.deepEqual(state.prepareCalls, [["", receiverModel.id, "r1"]]);
  assert.equal(button("Review installation"), undefined);
  assert.equal(button("Confirm firmware installation"), undefined);
  assert.deepEqual(state.installCalls, []);
});

test("a qualified target still requires explicit installation confirmation", async (t) => {
  const { state, receiverModel, receiver, button, click, renderedText } = await mount(t);
  state.nativeReady = true;
  await click("Prepare firmware");
  assert.deepEqual(state.prepareCalls, [[receiver.id, receiverModel.id, "r1"]]);
  assert.equal(button("Confirm firmware installation"), undefined);
  assert.deepEqual(state.installCalls, []);
  assert.ok(!renderedText().includes("Experimental update:"));

  await click("Review installation");
  assert.ok(button("Confirm firmware installation"));
  assert.deepEqual(state.installCalls, []);
  await click("Confirm firmware installation");
  assert.deepEqual(state.installCalls, ["job-1"]);
});

test("receiver recovery setup polls authorization to completion without writing firmware", async (t) => {
  const { state, button, click, renderedText } = await mount(t);
  assert.equal(state.recoveryChecks, 1, "Recovery status is checked on mount");
  await click("Set up receiver recovery");
  assert.equal(state.recoverySetupCalls, 1);
  assert.equal(button("Set up receiver recovery").props.disabled, true);
  assert.ok(renderedText().includes("Approve the SteamOS administrator prompt."));

  state.recoveryAccess = { configured: true, pending: false, can_setup: true, message: "Recovery USB access configured." };
  await settle(() => new Promise((resolve) => setTimeout(resolve, 1100)));
  assert.equal(state.recoveryChecks, 2);
  assert.equal(button("Set up receiver recovery"), undefined);
  assert.ok(renderedText().includes("Recovery USB access configured."));
  assert.deepEqual(state.prepareCalls, []);
  assert.deepEqual(state.installCalls, []);
});

test("recovery polling errors stop pending setup and remain visible", async (t) => {
  const { state, button, click, renderedText } = await mount(t);
  await click("Set up receiver recovery");
  state.recoveryError = new Error("Backend unavailable");
  await settle(() => new Promise((resolve) => setTimeout(resolve, 1100)));
  assert.ok(renderedText().includes("Could not check recovery access: Backend unavailable"));
  assert.equal(button("Set up receiver recovery").props.disabled, true);
  assert.deepEqual(state.installCalls, []);
});

test("experimental evidence is disclosed before review and still requires confirmation", async (t) => {
  const { state, button, click, renderedText } = await mount(t);
  state.nativeReady = true;
  state.evidenceLevel = "source-reviewed-experimental";
  await click("Prepare firmware");
  assert.ok(renderedText().includes("Experimental update: the protocol has been reviewed, but a complete update has not yet been validated on hardware."));
  assert.ok(button("Review installation"));
  assert.equal(button("Confirm firmware installation"), undefined);
  await click("Review installation");
  assert.ok(button("Confirm firmware installation"));
  assert.deepEqual(state.installCalls, []);
});
