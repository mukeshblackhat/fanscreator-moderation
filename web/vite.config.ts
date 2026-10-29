/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // VITE_POLLING=1 in the dev compose stack: file events from macOS bind mounts are unreliable.
  server: { host: "0.0.0.0", port: 5173, watch: process.env.VITE_POLLING === "1" ? { usePolling: true, interval: 300 } : undefined },
  preview: { host: "0.0.0.0", port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
