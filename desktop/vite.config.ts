import { defineConfig } from "vite";

export default defineConfig({
  root: "desktop",
  clearScreen: false,
  server: { host: "127.0.0.1", port: 1420, strictPort: true },
  build: { outDir: "../out/desktop", emptyOutDir: true, target: "es2020" },
});
