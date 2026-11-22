/**
 * The one browser origin whose commands this Worker accepts: the dashboard itself.
 *
 * The dashboard calls `/api/*` on its own host, so the Worker answers no cross-origin caller
 * and sends no CORS headers (local development reaches the API through the Vite dev proxy,
 * never through this Worker). The Origin check is the CSRF guard of every POST in index.ts.
 */
export const DASHBOARD_ORIGIN = "https://dashboard.origenlab.cl";

export function isAllowedOrigin(origin: string | null): origin is string {
  return origin === DASHBOARD_ORIGIN;
}
