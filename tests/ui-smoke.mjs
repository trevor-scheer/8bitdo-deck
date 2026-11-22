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
  const controllerModel = { ...receiverModel, id: "ultimate2-controller", role: "controller", download_only: true };
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
    evidenceLevel: undefined, evidence: undefined, recoveryChecks: 0, recoverySetupCalls: 0, recoveryError: null,
    recoveryAccess: { configured: false, pending: false, can_setup: true, message: "Recovery access needs setup." },
    interrupted: false, checkCalls: [], recoverCalls: [], jobChecks: [], finishRecovery: null,
    recoveryAction: "finish_boot_copy",
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
        evidence: state.evidence,
        can_check_recovery: state.interrupted,
      });
      return { job_id: id };
    },
    get_job: async (id) => { state.jobChecks.push(id); return state.jobs.get(id); },
    check_recovery: async (...args) => {
      state.checkCalls.push(args);
      const [id] = args;
      const job = { ...state.jobs.get(id), recovery_ready: true, recovery_action: state.recoveryAction };
      state.jobs.set(id, job);
      return job;
    },
    recover_update: async (...args) => {
      state.recoverCalls.push(args);
      const [id, confirmed] = args;
      assert.equal(confirmed, true, "Recovery requires explicit confirmation");
      if (state.finishRecovery) await state.finishRecovery;
      const job = { ...state.jobs.get(id), state: "queued", recovery_ready: false, can_check_recovery: false, message: "Recovery queued." };
      state.jobs.set(id, job);
      return job;
    },
    install_update: async (id, confirmed) => {
      assert.equal(confirmed, true, "Installation requires explicit confirmation");
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
    setup_recovery_access: async (confirmed) => {
      assert.equal(confirmed, true, "Permission setup requires explicit confirmation");
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

test("a verified wireless controller keeps its receiver-bound selection for preparation", async (t) => {
  const { state, controllerModel, receiver, button, click, renderedText } = await mount(t);
  controllerModel.download_only = false;
  controllerModel.wireless_only = true;
  state.devices = [{ ...receiver, id: "paired-aaaaaaaaaaaaaaaa", name: "Controller via receiver",
    model_id: controllerModel.id, role: "controller", backend: "wireless", status: "identified" }];
  await click("Scan connected devices");
  await click("Controller via receiver");
  assert.ok(button("Prepare firmware"));
  assert.ok(renderedText().includes("at least 30%"));
  await click("Prepare firmware");
  assert.equal(state.prepareCalls[0][0], "paired-aaaaaaaaaaaaaaaa");
  assert.equal(state.prepareCalls[0][1], controllerModel.id);
  assert.deepEqual(state.installCalls, []);
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
  await click("Download firmware only");
  assert.deepEqual(state.prepareCalls, [["", receiverModel.id, "r1"]]);
  assert.equal(button("Review installation"), undefined);
  assert.equal(button("Confirm firmware installation"), undefined);
  assert.deepEqual(state.installCalls, []);
  assert.equal(button("Check interrupted update"), undefined);
  assert.equal(button("Confirm receiver recovery"), undefined);
});

test("unsupported controllers disclose download-only behavior before preparation", async (t) => {
  const { state, controllerModel, modelPicker, button, click, renderedText } = await mount(t);
  await settle(() => modelPicker().props.onChange({ data: controllerModel.id }));
  assert.equal(button("Prepare firmware"), undefined);
  assert.ok(button("Download firmware only"));
  assert.ok(renderedText().includes("does not install firmware on this model"));
  await click("Download firmware only");
  assert.deepEqual(state.prepareCalls, [["", controllerModel.id, "r1"]]);
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

test("experimental review discloses detailed source evidence before installation confirmation", async (t) => {
  const { state, button, click, renderedText } = await mount(t);
  state.nativeReady = true;
  state.evidenceLevel = "source-reviewed-experimental";
  state.evidence = "Vendor source reviewed for this exact receiver and target image. Installed bootloader contents have not been read. First flash and recovery reflash remain untested on hardware.";
  await click("Prepare firmware");
  assert.ok(renderedText().includes("Experimental update: the protocol has been reviewed, but a complete update has not yet been validated on hardware."));
  assert.ok(button("Review installation"));
  assert.equal(button("Confirm firmware installation"), undefined);
  assert.ok(!renderedText().includes(state.evidence));
  await click("Review installation");
  assert.ok(renderedText().includes(state.evidence), "Review must show the backend's exact evidence before confirmation");
  assert.ok(button("Confirm firmware installation"));
  assert.deepEqual(state.installCalls, []);
  await click("Confirm firmware installation");
  assert.deepEqual(state.installCalls, ["job-1"]);
});

test("interrupted recovery requires checking and explicit confirmation, then resumes queued polling", async (t) => {
  const { state, button, click, renderedText } = await mount(t);
  state.interrupted = true;
  await click("Prepare firmware");
  assert.ok(button("Check interrupted update"));
  assert.equal(button("Confirm receiver recovery"), undefined);
  assert.deepEqual(state.recoverCalls, []);

  await click("Check interrupted update");
  assert.deepEqual(state.checkCalls, [["job-1"]]);
  assert.deepEqual(state.recoverCalls, [], "Checking must never start recovery");
  assert.equal(button("Check interrupted update"), undefined);
  assert.ok(renderedText().includes("Recovery may finish an already committed update. Keep USB connected."));
  assert.ok(button("Confirm receiver recovery"));

  let finishRecovery;
  state.finishRecovery = new Promise((resolve) => { finishRecovery = resolve; });
  await click("Confirm receiver recovery");
  assert.deepEqual(state.recoverCalls, [["job-1", true]]);
  assert.equal(button("Confirm receiver recovery").props.disabled, true);
  await settle(() => finishRecovery());
  assert.ok(renderedText().includes("Recovery queued."));
  assert.equal(button("Scan connected devices").props.disabled, true);

  const previousChecks = state.jobChecks.length;
  state.jobs.set("job-1", { ...state.jobs.get("job-1"), state: "complete", message: "Recovery verified." });
  await settle(() => new Promise((resolve) => setTimeout(resolve, 1100)));
  assert.ok(state.jobChecks.length > previousChecks, "Queued recovery must resume polling");
  assert.ok(renderedText().includes("Recovery verified."));
  assert.equal(button("Confirm receiver recovery"), undefined);
  assert.deepEqual(state.installCalls, []);
  assert.deepEqual(state.recoverCalls, [["job-1", true]]);
});

test("legacy recovery discloses erasing and reinstalling the same cached image before confirmation", async (t) => {
  const { state, button, click, renderedText } = await mount(t);
  state.interrupted = true;
  state.recoveryAction = "reflash";
  await click("Prepare firmware");
  assert.equal(button("Confirm erase and reinstall"), undefined);

  await click("Check interrupted update");
  assert.deepEqual(state.checkCalls, [["job-1"]]);
  assert.deepEqual(state.recoverCalls, [], "A recovery check must not erase or write firmware");
  assert.deepEqual(state.installCalls, []);
  assert.ok(renderedText().includes("Recovery will erase the receiver firmware and reinstall the exact cached image for this interrupted update."));
  assert.ok(!renderedText().includes("Recovery may finish an already committed update."));
  assert.equal(button("Confirm receiver recovery"), undefined);
  assert.ok(button("Confirm erase and reinstall"));

  await click("Confirm erase and reinstall");
  assert.deepEqual(state.recoverCalls, [["job-1", true]]);
  assert.deepEqual(state.installCalls, []);
  assert.equal(button("Confirm erase and reinstall"), undefined);
});
