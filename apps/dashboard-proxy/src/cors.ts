/** Browser origins allowed to call the dashboard read-only API proxy. */

import {
  isAllowedCommercialOperationsPostPath,
  isAllowedPostPath,
  stripApiPrefix,
} from "./allowlist";

export const ALLOWED_ORIGINS = new Set([
  "https://dashboard.origenlab.cl",
  "http://localhost:5173",
]);

export function isAllowedOrigin(origin: string | null): origin is string {
  return origin !== null && ALLOWED_ORIGINS.has(origin);
}

const UPSTREAM_CORS_HEADERS = [
  "Access-Control-Allow-Origin",
  "Access-Control-Allow-Credentials",
  "Access-Control-Allow-Methods",
  "Access-Control-Allow-Headers",
  "Access-Control-Expose-Headers",
  "Access-Control-Max-Age",
] as const;

/** Remove upstream CORS headers before applying Worker policy. */
export function stripUpstreamCorsHeaders(headers: Headers): void {
  for (const name of UPSTREAM_CORS_HEADERS) {
    headers.delete(name);
  }
}

/**
 * Apply dashboard CORS policy. With credentials: "include", Allow-Origin must be
 * the specific request Origin (never "*").
 */
export function applyCorsHeaders(request: Request, headers: Headers): void {
  headers.set("Vary", "Origin");

  const origin = request.headers.get("Origin");
  if (!isAllowedOrigin(origin)) {
    return;
  }

  headers.set("Access-Control-Allow-Origin", origin);
  headers.set("Access-Control-Allow-Credentials", "true");
  // POST is advertised only for explicitly sanctioned command paths.
  // index.ts remains the authoritative method+path enforcement layer.
  const upstreamPath = stripApiPrefix(new URL(request.url).pathname);
  const postAllowed =
    upstreamPath !== null && isAllowedPostPath(upstreamPath);

  headers.set(
    "Access-Control-Allow-Methods",
    postAllowed
      ? "GET, HEAD, OPTIONS, POST"
      : "GET, HEAD, OPTIONS",
  );

  const commercialCommand =
    upstreamPath !== null &&
    isAllowedCommercialOperationsPostPath(upstreamPath);

  headers.set(
    "Access-Control-Allow-Headers",
    commercialCommand
      ? "Accept, Content-Type, X-Request-ID, Idempotency-Key"
      : "Accept, Content-Type, X-Request-ID",
  );
  headers.set("Access-Control-Expose-Headers", "X-Request-ID");
}
