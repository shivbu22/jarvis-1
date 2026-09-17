import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/*
 * The port is pinned to 5173 because the gateway's CORS allow-list and the
 * status page link to exactly that origin. Changing it here means changing
 * DEFAULT_ORIGINS in apps/gateway/main.py too, or the browser will block every
 * request with a CORS error that looks like the gateway is down.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
