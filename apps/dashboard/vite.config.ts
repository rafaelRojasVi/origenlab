import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import type { IncomingMessage } from "node:http";
import { buildDevOperatorProxyHeaders } from "./vite.devOperatorProxy.ts";

/** Dev proxy target for apps/api (Dashboard-0 uses /health and /operator only). */
const apiTarget = process.env.VITE_ORIGENLAB_API_BASE_URL || "http://127.0.0.1:8001";

export default defineConfig(({ command }) => {
  const devOperatorHeaders = buildDevOperatorProxyHeaders(process.env, command);

  const proxyConfigure = devOperatorHeaders
    ? (proxy: {
        on: (
          event: "proxyReq",
          listener: (proxyReq: { setHeader: (name: string, value: string) => void }, req: IncomingMessage) => void,
        ) => void;
      }) => {
        proxy.on("proxyReq", (proxyReq) => {
          for (const [name, value] of Object.entries(devOperatorHeaders)) {
            proxyReq.setHeader(name, value);
          }
        });
      }
    : undefined;

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
