/** Production dashboard hostname (Cloudflare Access). */
export const DASHBOARD_PRODUCTION_HOST = "dashboard.origenlab.cl";

/** Local dev hostnames allowed in all build modes. */
export const DASHBOARD_LOCAL_DEV_HOSTS = ["localhost", "127.0.0.1"] as const;

const LOCAL_DEV_HOST_SET = new Set<string>(DASHBOARD_LOCAL_DEV_HOSTS);

export function normalizeDashboardHostname(hostname: string): string {
  return hostname.trim().toLowerCase();
}

export function isDashboardProductionBuild(mode: string = import.meta.env.MODE): boolean {
  return mode === "production";
}

/**
 * Whether the dashboard shell may mount the operator app and call operator APIs.
 * Production builds only allow the production host or local dev hostnames.
 */
export function isDashboardHostAllowed(
  hostname: string,
  options?: { productionBuild?: boolean },
): boolean {
  const host = normalizeDashboardHostname(hostname);
  if (!host) {
    return false;
  }
  if (LOCAL_DEV_HOST_SET.has(host)) {
    return true;
  }
  if (host === DASHBOARD_PRODUCTION_HOST) {
    return true;
  }
  const productionBuild = options?.productionBuild ?? isDashboardProductionBuild();
  if (!productionBuild) {
    return true;
  }
  return false;
}
