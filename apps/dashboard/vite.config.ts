import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { buildDevOperatorProxyConfigure } from "./vite.devOperatorProxy.ts";

/** Dev proxy target for apps/api (Dashboard-0 uses /health and /operator only). */
const apiTarget = process.env.VITE_ORIGENLAB_API_BASE_URL || "http://127.0.0.1:8001";

export default defineConfig(({ command }) => {
  // Always installed (never conditional on a trusted value being
  // configured): it must always strip an inbound browser-supplied
  // X-OriginLab-Operator-Email, whether or not it goes on to inject a
  // trusted server-side one.
  const proxyConfigure = buildDevOperatorProxyConfigure(process.env, command);

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/health": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
        "/operator": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
        "/cases": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
        "/opportunities": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
        "/contacts": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
        "/mirror": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
        "/operations": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      exclude: ["**/node_modules/**", "**/dist/**"],
    },
  };
});
