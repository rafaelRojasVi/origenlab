import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/** Dev proxy target for apps/api (Dashboard-0 uses /health and /operator only). */
const apiTarget = process.env.VITE_ORIGENLAB_API_BASE_URL || "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": { target: apiTarget, changeOrigin: true },
      "/operator": { target: apiTarget, changeOrigin: true },
      "/cases": { target: apiTarget, changeOrigin: true },
      "/opportunities": { target: apiTarget, changeOrigin: true },
      "/contacts": { target: apiTarget, changeOrigin: true },
      "/mirror": { target: apiTarget, changeOrigin: true },
      "/operations": { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    exclude: ["**/node_modules/**", "**/dist/**", "src/legacy/**"],
  },
});
