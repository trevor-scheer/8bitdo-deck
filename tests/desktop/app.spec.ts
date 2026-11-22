import { expect, test, type Page } from "@playwright/test";

async function mockBackend(
  page: Page,
  options: {
    nativeWrite?: boolean;
    recovery?: boolean;
    slowScan?: boolean;
    appInterrupted?: boolean;
  } = {},
) {
  await page.addInitScript((options) => {
    const models = [
      {
        id: "ultimate2-controller",
        name: "Ultimate 2 Wireless Controller",
        role: "controller",
        instructions: "Connect the controller by USB.",
        support: "Controller firmware installation is not qualified.",
      },
      {
        id: "ultimate2-receiver",
        name: "Ultimate 2 Wireless Receiver",
        role: "receiver",
        instructions: "Connect the receiver by USB.",
        support:
          "Experimental receiver updates require exact reviewed firmware.",
      },
      {
        id: "ultimate-bluetooth-controller",
        name: "Ultimate Bluetooth Controller",
        role: "controller",
        instructions: "Connect by USB.",
        support: "Download and identify only.",
      },
    ];
    const devices = models.slice(0, 2).map((model, i) => ({
      id: `device-${i}`,
      name: model.name,
      vid: "2dc8",
      pid: i ? "6013" : "301a",
      role: model.role,
      model_id: model.id,
      firmware_version: i ? "1.01" : "1.10",
      status: "identified",
      can_update: false,
      reason: "Exact image and device checks apply.",
    }));
    const fixture = {
      calls: [] as { operation: string; parameters: Record<string, unknown> }[],
      devices,
      models,
      jobs: {} as Record<string, Record<string, unknown>>,
      failScan: false,
      releaseScan: null as (() => void) | null,
    };
    const scope = window as unknown as {
      __fixture: typeof fixture;
      __TAURI_INTERNALS__: unknown;
      isTauri: boolean;
    };
    scope.__fixture = fixture;
    scope.isTauri = true;
    scope.__TAURI_INTERNALS__ = {
      invoke: async (
        command: string,
        request: { operation: string; parameters: Record<string, unknown> },
      ) => {
        if (command === "quit_application") return;
        const { operation, parameters } = request;
        fixture.calls.push({ operation, parameters });
        let result: unknown;
        switch (operation) {
          case "info":
            result = {
              app_version: "0.3.0-alpha.1",
              api_version: 1,
              platform: "linux",
              platform_supported: true,
              capabilities: { native_write: options.nativeWrite !== false },
              operations: {
                check_app_update: {},
                apply_app_update: {},
                recover_app_update: {},
              },
            };
            break;
          case "get_models":
            result = fixture.models;
            break;
          case "scan_devices":
            if (
              options.slowScan &&
              fixture.calls.filter((call) => call.operation === "scan_devices")
                .length > 1
            )
              await new Promise<void>((resolve) => {
                fixture.releaseScan = resolve;
              });
            if (fixture.failScan)
              throw new Error("Receiver access unavailable");
            result = { devices: fixture.devices, warnings: [] };
            break;
          case "get_releases":
            result = {
              releases: [
                {
                  id: "official-1.06",
                  version: "1.06",
                  date: "2026-10-15",
                  notes:
                    "Improves receiver stability.\nFixes reconnect behavior.",
                  downloadable: true,
                },
              ],
            };
            break;
          case "prepare_update":
            fixture.jobs.job1 = {
              id: "job1",
              state: options.recovery ? "failed" : "ready",
              progress: 100,
              can_install:
                !options.recovery &&
                !!parameters.device_id &&
                options.nativeWrite !== false,
              target_label: "Ultimate 2 receiver · 1.01 → 1.06",
              message: "Exact reviewed image is ready.",
              evidence_level: "source-reviewed-experimental",
              evidence: "First hardware flash remains unvalidated.",
              can_check_recovery: !!options.recovery,
            };
            result = { job_id: "job1" };
            break;
          case "get_job":
            result = fixture.jobs.job1;
            break;
          case "install_update":
            fixture.jobs.job1 = {
              ...fixture.jobs.job1,
              state: "complete",
              can_install: false,
              message: "Receiver firmware verified.",
            };
            result = fixture.jobs.job1;
            break;
          case "check_recovery":
            fixture.jobs.job1 = {
              ...fixture.jobs.job1,
              recovery_ready: true,
              recovery_action: "reflash",
            };
            result = fixture.jobs.job1;
            break;
          case "recover_update":
            fixture.jobs.job1 = {
              ...fixture.jobs.job1,
              recovery_ready: false,
              state: "complete",
              message: "Recovery verified.",
            };
            result = fixture.jobs.job1;
            break;
          case "get_recovery_access":
            result = {
              configured: true,
              pending: false,
              can_setup: false,
              message: "Recovery access is configured.",
            };
            break;
          case "check_app_update":
            result = {
              current_version: "0.3.0-alpha.1",
              channel: parameters.channel,
              available_version: "0.3.0a2",
              release_notes: "Improved controller discovery.",
              state: options.appInterrupted ? "interrupted" : "available",
              can_recover: !!options.appInterrupted,
              can_apply: !options.appInterrupted,
              restart_required: false,
              message: "A verified update is available.",
            };
            break;
          case "recover_app_update":
            result = {
              state: "recovered",
              can_apply: false,
              can_recover: false,
              restart_required: true,
              message: "Previous application restored.",
            };
            break;
          case "apply_app_update":
            result = {
              state: "staged",
              can_apply: false,
              restart_required: true,
              message: "Quit the application to finish updating.",
            };
            break;
          default:
            throw new Error(`Unexpected mock operation: ${operation}`);
        }
        return { api_version: 1, ok: true, result };
      },
    };
  }, options);
}

async function calls(page: Page, operation: string) {
  return page.evaluate(
    (name) =>
      (
        window as unknown as {
          __fixture: {
            calls: { operation: string; parameters: Record<string, unknown> }[];
          };
        }
      ).__fixture.calls.filter((call) => call.operation === name),
    operation,
  );
}
async function receiver(page: Page) {
  await page
    .getByRole("button", { name: /Connected.*Ultimate 2 Wireless Receiver/ })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Firmware release", exact: true }),
  ).toHaveValue("official-1.06");
}

test("connected pictures and separate component versions are visible; confirmation gates writes", async ({
  page,
}, testInfo) => {
  await mockBackend(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "My devices", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Firmware 1.10/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Firmware 1.01/ }),
  ).toBeVisible();
  await expect(page.locator(".device-card img")).toHaveCount(2);
  await receiver(page);
  await page.screenshot({
    path: testInfo.outputPath("connected-devices.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Prepare firmware" }).click();
  await page.getByRole("button", { name: "Review installation" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "First hardware flash remains unvalidated.",
  );
  expect(await calls(page, "install_update")).toHaveLength(0);
  await page.getByRole("button", { name: "Go back" }).press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "Review installation" }).click();
  await page
    .getByRole("button", { name: "Confirm firmware installation" })
    .click();
  expect((await calls(page, "install_update"))[0].parameters).toEqual({
    job_id: "job1",
    confirmed: true,
  });
  await expect(page.getByText("Receiver firmware verified.")).toBeVisible();
});

test("library browsing cannot bind a download to an attached device", async ({
  page,
}) => {
  await mockBackend(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Device library", exact: true })
    .click();
  await page.getByLabel("Search device library").fill("Receiver");
  await page
    .getByRole("button", { name: /Catalog.*Ultimate 2 Wireless Receiver/ })
    .click();
  await page.getByRole("button", { name: "Prepare firmware" }).click();
  expect((await calls(page, "prepare_update"))[0].parameters.device_id).toBe(
    "",
  );
  await expect(
    page.getByRole("button", { name: "Review installation" }),
  ).toHaveCount(0);
});

test("a pending scan does not overwrite a newer library selection", async ({
  page,
}) => {
  await mockBackend(page, { slowScan: true });
  await page.goto("/");
  await receiver(page);
  await page.getByRole("button", { name: "Scan devices" }).click();
  await page
    .getByRole("button", { name: "Device library", exact: true })
    .click();
  await page
    .getByRole("button", { name: /Catalog.*Ultimate Bluetooth Controller/ })
    .click();
  await page.evaluate(() =>
    (
      window as unknown as { __fixture: { releaseScan: () => void } }
    ).__fixture.releaseScan(),
  );
  await expect(
    page
      .getByRole("complementary", { name: "Firmware details" })
      .getByRole("heading", { name: "Ultimate Bluetooth Controller" }),
  ).toBeVisible();
});

test("recovery inspection cannot write and destructive recovery is separately confirmed", async ({
  page,
}) => {
  await mockBackend(page, { recovery: true });
  await page.goto("/");
  await receiver(page);
  await page.getByRole("button", { name: "Prepare firmware" }).click();
  await page.getByRole("button", { name: "Check interrupted update" }).click();
  expect(await calls(page, "recover_update")).toHaveLength(0);
  await page.getByRole("button", { name: "Review recovery" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "erase the receiver firmware",
  );
  await page
    .getByRole("button", { name: "Confirm erase and reinstall" })
    .click();
  expect((await calls(page, "recover_update"))[0].parameters).toEqual({
    job_id: "job1",
    confirmed: true,
  });
});

test("app updates bind confirmation to the reviewed version", async ({
  page,
}) => {
  await mockBackend(page);
  await page.goto("/");
  await page.getByRole("button", { name: "App settings" }).click();
  await page.getByRole("button", { name: "Check for app updates" }).click();
  await page.getByRole("button", { name: "Review app update" }).click();
  expect(await calls(page, "apply_app_update")).toHaveLength(0);
  await page.getByRole("button", { name: "Confirm app update" }).click();
  expect((await calls(page, "apply_app_update"))[0].parameters).toEqual({
    channel: "stable",
    version: "0.3.0a2",
    confirmed: true,
  });
  await expect(
    page.getByText("Quit the application to finish updating."),
  ).toBeVisible();
});

test("platform limits are visible and installation stays disabled", async ({
  page,
}) => {
  await mockBackend(page, { nativeWrite: false });
  await page.goto("/");
  await receiver(page);
  await expect(
    page.getByText("Discovery and downloads are available on this platform."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Prepare firmware" }).click();
  await expect(
    page.getByRole("button", { name: "Review installation" }),
  ).toHaveCount(0);
});

test("browser preview never fabricates devices and reports its missing bridge", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Browser preview cannot access your devices.",
  );
  await expect(page.locator(".device-card")).toHaveCount(0);
});

test("a rescan removes a disconnected target before another preparation", async ({
  page,
}) => {
  await mockBackend(page);
  await page.goto("/");
  await receiver(page);
  await page.evaluate(() => {
    (
      window as unknown as { __fixture: { devices: unknown[] } }
    ).__fixture.devices = [];
  });
  await page.getByRole("button", { name: "Scan devices" }).click();
  await expect(
    page.getByRole("heading", { name: "Choose a component" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Prepare firmware" }),
  ).toHaveCount(0);
  expect(await calls(page, "prepare_update")).toHaveLength(0);
});

test("interrupted application rollback needs explicit confirmation", async ({
  page,
}) => {
  await mockBackend(page, { appInterrupted: true });
  await page.goto("/");
  await page.getByRole("button", { name: "App settings" }).click();
  await page.getByRole("button", { name: "Check for app updates" }).click();
  await page
    .getByRole("button", { name: "Review app update rollback" })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "This does not write firmware.",
  );
  expect(await calls(page, "recover_app_update")).toHaveLength(0);
  await page.getByRole("button", { name: "Confirm app rollback" }).click();
  expect((await calls(page, "recover_app_update"))[0].parameters).toEqual({
    confirmed: true,
  });
  await expect(page.getByText("Previous application restored.")).toBeVisible();
});
