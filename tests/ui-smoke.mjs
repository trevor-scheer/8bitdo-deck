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
    connect: () => ({ _version: 2, callable: (name) => (...args) => handlers[name](...args) }),
  },
};
const { default: plugin } = await import("../dist/index.js");

async function settle(action) {
  await act(async () => {
    await action?.();
    for (let step = 0; step < 80; step++) await Promise.resolve();
  });
}

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const release = (id = "r2", version = "1.06", downloadable = true) => ({
  id, version, downloadable, date: "", notes: "Improves reconnecting.",
});
function visibleText(value) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(visibleText).join(" ");
  if (!value || typeof value !== "object") return "";
  return [value.children, value.props?.children, value.props?.label, value.props?.description].map(visibleText).join(" ");
}
const receiverModel = {
  id: "ultimate2-receiver", name: "Ultimate 2 Wireless", role: "receiver",
  instructions: "Connect receiver by USB.", support: "Reviewed updates only.",
};
const controllerModel = {
  ...receiverModel, id: "ultimate2-controller", role: "controller", download_only: true,
};
const receiver = {
  id: "usb-1111111111111111", name: "Ultimate 2 receiver", vid: "2dc8", pid: "6013", role: "receiver",
  model_id: receiverModel.id, firmware_version: "1.01", status: "identified", can_update: false,
  reason: "Preparation checks this receiver.", backend: "hid",
};
const unknown = {
  ...receiver, id: "usb-2222222222222222", name: "Unknown controller", model_id: null,
  role: "unknown", status: "ambiguous", firmware_version: null, reason: "Identify exact model first.",
};

async function mount(t, configure = () => {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = {
    devices: [receiver, unknown], models: [receiverModel, controllerModel],
    releases: [release("old", "1.02"), release()], releaseOverride: null,
    scanCalls: 0, releaseCalls: [], prepareCalls: [], installCalls: [], checkCalls: [], recoverCalls: [],
    jobs: new Map(), jobChecks: [], jobError: null, queue: null, queueError: null,
    prepareResult: null, installError: null, events: [], storage: new Map(),
    access: { configured: true, pending: false, can_setup: false, message: "USB recovery access ready." },
    setupCalls: [],
  };
  configure(state);
  globalThis.localStorage = {
    getItem: (key) => state.storage.get(key) ?? null,
    setItem: (key, value) => state.storage.set(key, value),
    removeItem: (key) => state.storage.delete(key),
  };
  const withChildren = (job) => job?.jobs ? {
    ...job, jobs: job.jobs.map((child) => state.jobs.get(child.id) ?? child),
  } : job;
  handlers = {
    get_update_queue: async () => {
      if (state.queueError) throw state.queueError;
      return withChildren(state.queue);
    },
    scan_devices: async () => { state.scanCalls++; return { devices: state.devices, warnings: [] }; },
    get_models: async () => state.models,
    get_releases: async (id) => {
      state.releaseCalls.push(id);
      return state.releaseOverride ? state.releaseOverride(id) : { releases: state.releases };
    },
    prepare_update: async (...args) => {
      state.prepareCalls.push(args);
      const id = `job-${state.prepareCalls.length}`;
      state.events.push(`prepare:${id}`);
      const downloadOnly = !args[0] || state.models.find((model) => model.id === args[1])?.download_only;
      const result = state.prepareResult?.(id, args) ?? {
        id, state: downloadOnly ? "blocked" : "ready", progress: 100,
        message: downloadOnly ? "Download only. This model cannot be installed." : "Firmware checks passed.",
        can_install: !downloadOnly, target_label: `${args[1]} → 1.06`,
        evidence_level: "source-reviewed-experimental", evidence: "Protocol reviewed; hardware write not validated.",
      };
      state.jobs.set(id, result);
      return { job_id: id };
    },
    get_job: async (id) => {
      state.jobChecks.push(id);
      state.events.push(`read:${id}`);
      const error = state.jobError?.(id);
      if (error) throw error;
      const job = state.queue?.id === id ? withChildren(state.queue) : state.jobs.get(id);
      if (!job) throw new Error("Job not found.");
      return job;
    },
    install_updates: async (ids, confirmed) => {
      assert.equal(confirmed, true, "The Install click confirms the selected jobs");
      state.installCalls.push([...ids]);
      const jobs = ids.map((id) => {
        const job = { ...state.jobs.get(id), state: "queued", can_install: false };
        state.jobs.set(id, job);
        return job;
      });
      state.queue = { id: "batch-1", state: "installing", progress: 0, message: "Installing selected updates.", can_install: false, jobs };
      if (state.installError) throw state.installError;
      return state.queue;
    },
    install_update: async () => { throw new Error("The compact UI must use the durable queue."); },
    check_recovery: async (id) => {
      state.checkCalls.push(id);
      const job = { ...state.jobs.get(id), recovery_ready: true, recovery_action: "reflash" };
      state.jobs.set(id, job);
      return job;
    },
    recover_update: async (id, confirmed) => {
      assert.equal(confirmed, true);
      state.recoverCalls.push(id);
      const job = { ...state.jobs.get(id), state: "queued", recovery_ready: false, can_check_recovery: false };
      state.jobs.set(id, job);
      return job;
    },
    get_recovery_access: async () => state.access,
    setup_recovery_access: async (confirmed) => { state.setupCalls.push(confirmed); return state.access; },
    export_diagnostics: async () => ({ path: "/tmp/diagnostics.json" }),
    open_official_updater: async () => ({ opened: true }),
  };
  let renderer;
  const render = async () => { await settle(() => { renderer = TestRenderer.create(plugin().content); }); };
  const unmount = async () => {
    if (renderer) await settle(() => { renderer.unmount(); renderer = null; });
  };
  await render();
  t.after(unmount);
  const buttons = (label) => renderer.root.findAllByType("ButtonItem").filter((item) => item.props.children === label);
  const button = (label) => buttons(label)[0];
  const rowButton = (name) => renderer.root.findAllByType("ButtonItem").find((item) => item.props.label?.props?.children === name);
  const click = async (label) => {
    const item = button(label);
    assert.ok(item, `Missing button: ${label}`);
    assert.equal(Boolean(item.props.disabled), false, `Button is disabled: ${label}`);
    await settle(() => item.props.onClick());
  };
  const tick = async (milliseconds) => settle(() => t.mock.timers.tick(milliseconds));
  return {
    state, button, buttons, rowButton, click, tick, unmount,
    remount: async () => { await unmount(); await render(); },
    text: () => visibleText(renderer.toJSON()),
    dropdown: (label) => renderer.root.findAllByType("DropdownItem").find((item) => item.props.label === label),
  };
}

test("identified devices automatically prepare latest firmware once across panel remounts", async (t) => {
  const { state, button, rowButton, remount } = await mount(t, (state) => {
    state.devices.push({ ...receiver, id: "usb-3333333333333333", name: "Second receiver" });
  });
  assert.deepEqual(state.prepareCalls, [
    [receiver.id, receiver.model_id, "r2"], ["usb-3333333333333333", receiver.model_id, "r2"],
  ]);
  assert.ok(state.events.indexOf("read:job-1") < state.events.indexOf("prepare:job-2"));
  assert.equal(state.releaseCalls.length, 1, "Same-model devices share a catalog query");
  assert.equal(rowButton(unknown.name).props.children, "Details");
  assert.equal(rowButton(receiver.name).props.children, "Install");
  assert.ok(button("Update all (2)"));
  assert.deepEqual(state.installCalls, [], "Automatic preparation must never write firmware");
  await remount();
  assert.equal(state.prepareCalls.length, 2, "Prepared job cache survives panel remount");
  assert.deepEqual(state.installCalls, []);
});

test("preparation waits for each background download before probing another device", async (t) => {
  const { state, tick, button } = await mount(t, (state) => {
    state.devices = [receiver, { ...receiver, id: "usb-3333333333333333", name: "Second receiver" }];
    state.prepareResult = (id) => id === "job-1" ? { id, state: "downloading", progress: 10, can_install: false, message: "Downloading." } : null;
  });
  assert.equal(state.prepareCalls.length, 1);
  assert.equal(button("Update all").props.disabled, true);
  state.jobs.set("job-1", { ...state.jobs.get("job-1"), state: "ready", can_install: true });
  await tick(1000);
  assert.equal(state.prepareCalls.length, 2);
  assert.ok(button("Update all (2)"));
  assert.deepEqual(state.installCalls, []);
});

test("current devices skip downloads and unsupported devices retain download-only status", async (t) => {
  const { state, rowButton, text, button } = await mount(t, (state) => {
    state.devices = [
      { ...receiver, firmware_version: "1.06" }, unknown,
      { ...receiver, id: "usb-3333333333333333", model_id: controllerModel.id, name: "USB controller", role: "controller" },
    ];
  });
  assert.deepEqual(state.prepareCalls, [["usb-3333333333333333", controllerModel.id, "r2"]]);
  assert.ok(text().includes("Up to date"));
  assert.ok(text().includes("Install unavailable"));
  assert.equal(rowButton("USB controller").props.children, "Details");
  assert.equal(button("Update all").props.disabled, true);
  assert.deepEqual(state.installCalls, []);
});

test("one Install click starts its exact prepared job without a review screen", async (t) => {
  const { state, click, button, rowButton, text } = await mount(t);
  assert.ok(text().includes("Experimental"));
  assert.equal(button("Review installation"), undefined);
  assert.equal(button("Confirm firmware installation"), undefined);
  await click("Install");
  assert.deepEqual(state.installCalls, [["job-1"]]);
  assert.equal(rowButton(receiver.name).props.children === "Install" && !rowButton(receiver.name).props.disabled, false);
  assert.equal(button("Check for updates").props.disabled, true);
});

test("Update all snapshots all ready jobs once and excludes blocked targets", async (t) => {
  const { state, click, button } = await mount(t, (state) => {
    state.devices.push(
      { ...receiver, id: "usb-3333333333333333", name: "Second receiver" },
      { ...receiver, id: "usb-4444444444444444", name: "USB controller", model_id: controllerModel.id },
    );
  });
  await click("Update all (2)");
  assert.deepEqual(state.installCalls, [["job-1", "job-2"]]);
  assert.equal((button("Update all (2)") ?? button("Update all")).props.disabled, true);
  assert.equal(state.prepareCalls.length, 3);
});

test("an active backend queue restores without scanning or preparing hardware", async (t) => {
  const { state, button, tick } = await mount(t, (state) => {
    state.queue = { id: "existing-batch", state: "installing", progress: 42, message: "Already installing.", can_install: false, jobs: [] };
  });
  assert.equal(state.scanCalls, 0);
  assert.deepEqual(state.prepareCalls, []);
  assert.equal(button("Check for updates").props.disabled, true);
  await tick(1000);
  assert.equal(state.scanCalls, 0);
  assert.deepEqual(state.installCalls, []);
});

test("lost queue progress keeps writes disabled until status is recovered", async (t) => {
  const { state, click, button, tick, text } = await mount(t);
  await click("Install");
  state.jobError = (id) => id === "batch-1" ? new Error("Transport unavailable") : null;
  await tick(1000);
  assert.ok(text().includes("Update status unavailable"));
  assert.equal((button("Update all (1)") ?? button("Update all")).props.disabled, true);
  assert.equal(button("Check for updates").props.disabled, true);
  assert.equal(button("Dismiss status"), undefined);
  state.jobError = null;
  await tick(3000);
  assert.equal((button("Update all (1)") ?? button("Update all")).props.disabled, true, "Recovered active queue is still locked");
  assert.deepEqual(state.installCalls, [["job-1"]]);
});

test("a lost install response reconciles the accepted queue without another write", async (t) => {
  const { state, click, button } = await mount(t, (state) => { state.installError = new Error("Lost response"); });
  await click("Install");
  assert.equal(button("Check for updates").props.disabled, true);
  assert.deepEqual(state.installCalls, [["job-1"]]);
});

test("interrupted batch recovery acts on its child job and remains explicit", async (t) => {
  const { state, click, button } = await mount(t, (state) => {
    state.devices = [];
    const child = { id: "interrupted-child", state: "failed", progress: 50, can_install: false, message: "Interrupted write.", can_check_recovery: true, target_label: "Receiver" };
    state.jobs.set(child.id, child);
    state.queue = { id: "failed-batch", state: "failed", progress: 50, can_install: false, message: "Stopped after failure.", jobs: [child] };
  });
  assert.deepEqual(state.recoverCalls, []);
  await click("Check interrupted update");
  assert.deepEqual(state.checkCalls, ["interrupted-child"]);
  assert.ok(button("Erase and reinstall"));
  assert.deepEqual(state.recoverCalls, []);
  await click("Erase and reinstall");
  assert.deepEqual(state.recoverCalls, ["interrupted-child"]);
  assert.equal(button("Check for updates").props.disabled, true);
  assert.deepEqual(state.installCalls, []);
});

test("manual options start collapsed and downloads never bind connected hardware", async (t) => {
  const { state, button, click, dropdown } = await mount(t);
  assert.equal(dropdown("Browse firmware"), undefined);
  assert.equal(button("Export diagnostics"), undefined);
  await click("More options");
  await settle(() => dropdown("Browse firmware").props.onChange({ data: receiverModel.id }));
  await click("Download firmware only");
  assert.deepEqual(state.prepareCalls.at(-1), ["", receiverModel.id, "old"]);
  assert.deepEqual(state.installCalls, []);
  assert.equal(button("Review installation"), undefined);
});

test("an unavailable latest firmware never falls back to an older release", async (t) => {
  const { state, text, button } = await mount(t, (state) => {
    state.releases = [release("old", "1.05"), release("newest", "1.06", false)];
  });
  assert.deepEqual(state.prepareCalls, []);
  assert.ok(text().includes("Update unavailable"));
  assert.equal(button("Update all").props.disabled, true);
});

test("an unsupported latest image stays blocked instead of preparing an older image", async (t) => {
  const { state, button } = await mount(t, (state) => {
    state.prepareResult = (id) => ({ id, state: "blocked", can_install: false, progress: 100, message: "This newest image has not been reviewed." });
  });
  assert.deepEqual(state.prepareCalls, [[receiver.id, receiver.model_id, "r2"]]);
  assert.equal(button("Update all").props.disabled, true);
  assert.deepEqual(state.installCalls, []);
});

test("a changed detected identity invalidates cached preparation on remount", async (t) => {
  const { state, remount } = await mount(t);
  state.devices = [{ ...receiver, firmware_version: "1.02" }];
  await remount();
  assert.equal(state.prepareCalls.length, 2);
  assert.deepEqual(state.installCalls, []);
});

test("catalog completion after panel unmount cannot start another preparation", async (t) => {
  const pending = deferred();
  const { state, unmount } = await mount(t, (state) => { state.releaseOverride = () => pending.promise; });
  assert.deepEqual(state.prepareCalls, []);
  await unmount();
  await settle(() => pending.resolve({ releases: [release()] }));
  assert.deepEqual(state.prepareCalls, []);
  assert.deepEqual(state.installCalls, []);
});

test("a completed queue rescans and prepares newly eligible devices without extending consent", async (t) => {
  const { state, click, tick, button } = await mount(t);
  await click("Install");
  state.jobs.set("job-1", { ...state.jobs.get("job-1"), state: "complete", progress: 100 });
  state.queue = { ...state.queue, state: "complete", progress: 100, message: "All updates verified." };
  state.models = [receiverModel, { ...controllerModel, download_only: false, wireless_only: true }];
  state.devices = [
    { ...receiver, firmware_version: "1.06" },
    { ...receiver, id: "paired-1111111111111111", name: "Controller via receiver", model_id: controllerModel.id,
      firmware_version: "1.09", role: "controller", backend: "wireless" },
  ];
  state.releaseOverride = (id) => ({ releases: [id === controllerModel.id ? release("controller-new", "1.10") : release()] });
  await tick(1000);
  assert.equal(state.scanCalls, 2);
  assert.deepEqual(state.prepareCalls.at(-1), ["paired-1111111111111111", controllerModel.id, "controller-new"]);
  assert.deepEqual(state.installCalls, [["job-1"]], "Newly eligible updates require another Install click");
  assert.ok(button("Update all (1)"));
  assert.equal(button("Update all (1)").props.disabled, false);
});

test("failed and cancelled batch children cannot immediately replay their prepared jobs", async (t) => {
  const { state, click, tick, button, text } = await mount(t, (state) => {
    state.devices = [receiver, { ...receiver, id: "usb-3333333333333333", name: "Second receiver" }];
  });
  await click("Update all (2)");
  state.jobs.set("job-1", { ...state.jobs.get("job-1"), state: "failed", message: "Interrupted update.", can_check_recovery: true });
  state.jobs.set("job-2", { ...state.jobs.get("job-2"), state: "cancelled", message: "Not started." });
  state.queue = { ...state.queue, state: "failed", message: "Stopped after first failure." };
  await tick(1000);
  assert.ok(text().includes("Update failed"));
  assert.ok(text().includes("Not updated"));
  assert.equal(button("Update all").props.disabled, true);
  assert.equal(button("Install"), undefined);
  assert.equal(state.scanCalls, 1, "An interrupted write must not cause automatic device probes");
  assert.deepEqual(state.installCalls, [["job-1", "job-2"]]);
});

test("uncertain queue restoration blocks scanning and installing", async (t) => {
  const { state, button, text } = await mount(t, (state) => { state.queueError = new Error("Backend unavailable"); });
  assert.equal(state.scanCalls, 0);
  assert.deepEqual(state.prepareCalls, []);
  assert.ok(text().includes("Could not restore update status"));
  assert.equal(button("Check for updates").props.disabled, true);
  assert.equal(button("Update all").props.disabled, true);
});

test("rapid repeated Install callbacks launch only one batch", async (t) => {
  const { state, button } = await mount(t);
  const onClick = button("Install").props.onClick;
  await settle(() => { onClick(); onClick(); });
  assert.deepEqual(state.installCalls, [["job-1"]]);
});
