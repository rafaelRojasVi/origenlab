import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { buildDevOperatorProxyConfigure } from "./vite.devOperatorProxy.ts";

/**
 * Dev proxy target for apps/api (Dashboard-0 uses /health and /operator only).
 *
 * `ORIGENLAB_DEV_API_TARGET` moves only the proxy target, for the ordinary case of a second
 * local API on another port -- another checkout is already holding 8001, say. It is
 * deliberately NOT `VITE_`-prefixed: `VITE_ORIGENLAB_API_BASE_URL` is also read by client
 * code through `import.meta.env`, so using that one to move the target makes the browser
 * call the API directly and lose the operator header this proxy injects.
 */
const apiTarget =
  process.env.ORIGENLAB_DEV_API_TARGET ||
  process.env.VITE_ORIGENLAB_API_BASE_URL ||
  "http://127.0.0.1:8001";

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
        // The V2 durable read boundary. It goes through the same configure hook as every
        // other prefix, so the dev server strips a browser-supplied operator header and
        // injects the trusted one -- exactly what the production Worker does. Without this
        // entry the V2 pages 404 in `npm run dev` while working in production, which is the
        // worst shape a routing gap can take.
        "/v2": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
        // Dashboard sign-in. Same origin as the page, so the API's HttpOnly session cookie
        // lands on localhost:5173 and is sent back on every /v2 call. The redirect to Google
        // and back passes through unchanged; register the resulting redirect URI,
        // http://localhost:5173/auth/google/callback, in Google Cloud Console.
        "/auth": { target: apiTarget, changeOrigin: true, configure: proxyConfigure },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      exclude: ["**/node_modules/**", "**/dist/**"],
    },
  };
});
