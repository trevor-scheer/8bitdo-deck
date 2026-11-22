import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../tests/desktop",
  fullyParallel: true,
  use: {
    baseURL: "http://127.0.0.1:1420",
    viewport: { width: 1280, height: 900 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "pnpm run dev:desktop",
    url: "http://127.0.0.1:1420",
    reuseExistingServer: false,
  },
});
