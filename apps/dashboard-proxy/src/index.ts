import {
  marketingCommandMaxBytes,
  isAllowedMarketingCommandPostPath,
  isAllowedPostPath,
  isAllowedPostUploadPath,
  isAllowedUpstreamPath,
  stripApiPrefix,
} from "./allowlist";
import { filterAuthSetCookies, isAllowedAuthRedirect, isAuthPath } from "./auth";
import { applyCorsHeaders, isAllowedOrigin, stripUpstreamCorsHeaders } from "./cors";
import { API_AUTH_HEADER, IDEMPOTENCY_KEY_HEADER, buildUpstreamHeaders, buildUpstreamUrl, type ProxyEnv } from "./proxy";

export type { ProxyEnv } from "./proxy";
export {
  stripApiPrefix,
  isAllowedUpstreamPath,
  isAllowedPostUploadPath,
  isAllowedPostPath,
  buildUpstreamUrl,
  buildUpstreamHeaders,
  API_AUTH_HEADER,
};
export { ALLOWED_ORIGINS, applyCorsHeaders, isAllowedOrigin } from "./cors";

const PROXY_MARKER = "dashboard-proxy";
const CACHE_CONTROL_NO_STORE = "no-store, private";

function applyProxyDiagnosticHeaders(headers: Headers, upstreamStatus?: number): void {
  headers.set("X-OriginLab-Proxy", PROXY_MARKER);
  if (upstreamStatus !== undefined) {
    headers.set("X-OriginLab-Upstream-Status", String(upstreamStatus));
  }
}

function jsonError(request: Request, status: number, code: string): Response {
  const headers = new Headers({
    "Content-Type": "application/json",
    "Cache-Control": CACHE_CONTROL_NO_STORE,
  });
  applyCorsHeaders(request, headers);
  applyProxyDiagnosticHeaders(headers);
  return new Response(JSON.stringify({ error: { code } }), {
    status,
    headers,
  });
}

function blockedRedirectResponse(
  request: Request,
  method: string,
  upstreamStatus: number,
): Response {
  const headers = new Headers({
    "Content-Type": "application/json",
    "Cache-Control": CACHE_CONTROL_NO_STORE,
  });
  applyCorsHeaders(request, headers);
  applyProxyDiagnosticHeaders(headers, upstreamStatus);

  return new Response(
    method === "HEAD" ? null : JSON.stringify({ error: { code: "upstream_redirect_blocked" } }),
    { status: 502, headers },
  );
}

/**
 * A sign-in redirect, rebuilt rather than copied: the Location that was checked, the two
 * sign-in cookies and nothing else from upstream, and never cacheable.
 */
function authRedirectResponse(upstreamResponse: Response, location: string): Response {
  const headers = new Headers({
    Location: location,
    "Cache-Control": CACHE_CONTROL_NO_STORE,
    "Referrer-Policy": "no-referrer",
  });
  for (const cookie of filterAuthSetCookies(upstreamResponse.headers)) {
    headers.append("Set-Cookie", cookie);
  }
  const requestId = upstreamResponse.headers.get("X-Request-ID");
  if (requestId) {
    headers.set("X-Request-ID", requestId);
  }
  applyProxyDiagnosticHeaders(headers, upstreamResponse.status);
  return new Response(null, { status: upstreamResponse.status, headers });
}

const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9._:-]{8,128}$/;

/**
 * The CSRF and replay guard for the marketing commands, checked before anything is forwarded.
 * The session cookie is SameSite=Lax and HttpOnly; these checks do not rely on that alone:
 * a cross-site form or script cannot send an allowed `Origin`, a JSON `Content-Type` without a
 * preflight this Worker only answers for allowed origins, or a custom `Idempotency-Key`.
 */
export function marketingCommandRefusal(request: Request): { status: number; code: string } | null {
  if (!isAllowedOrigin(request.headers.get("Origin"))) {
    return { status: 403, code: "origin_not_allowed" };
  }
  const site = request.headers.get("Sec-Fetch-Site");
  if (site !== null && site !== "same-origin" && site !== "same-site") {
    return { status: 403, code: "cross_site_request" };
  }
  const contentType = (request.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    return { status: 415, code: "unsupported_media_type" };
  }
  if (!IDEMPOTENCY_KEY_RE.test(request.headers.get(IDEMPOTENCY_KEY_HEADER) || "")) {
    return { status: 400, code: "idempotency_key_required" };
  }
  const length = Number(request.headers.get("Content-Length") || "0");
  const upstreamPath = stripApiPrefix(new URL(request.url).pathname) ?? "";
  if (!Number.isFinite(length) || length > marketingCommandMaxBytes(upstreamPath)) {
    return { status: 413, code: "payload_too_large" };
  }
  return null;
}

const ALLOWED_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function handleRequest(request: Request, env: ProxyEnv): Promise<Response> {
  const url = new URL(request.url);
  const method = request.method.toUpperCase();

  if (method === "OPTIONS") {
    const headers = new Headers();
    applyCorsHeaders(request, headers);
    applyProxyDiagnosticHeaders(headers);
    return new Response(null, { status: 204, headers });
  }

  const upstreamPath = stripApiPrefix(url.pathname);

  // Method+path authorization, never method-only authorization.
  //
  // POST is legal only for explicit annex-bundle commands and the narrowly
  // enumerated commercial-operations commands. GET allowlisting never
  // implies POST permission.
  if (method === "POST") {
    if (upstreamPath === null || !isAllowedPostPath(upstreamPath)) {
      return jsonError(request, 405, "method_not_allowed");
    }
    if (isAllowedMarketingCommandPostPath(upstreamPath)) {
      const refusal = marketingCommandRefusal(request);
      if (refusal) {
        return jsonError(request, refusal.status, refusal.code);
      }
    }
  } else if (MUTATING_METHODS.has(method)) {
    return jsonError(request, 405, "method_not_allowed");
  } else if (!ALLOWED_METHODS.has(method)) {
    return jsonError(request, 405, "method_not_allowed");
  } else if (upstreamPath === null) {
    return jsonError(request, 404, "not_found");
  } else if (!isAllowedUpstreamPath(upstreamPath)) {
    return jsonError(request, 403, "path_not_allowed");
  }

  const upstreamBase = env.ORIGENLAB_API_UPSTREAM?.trim();
  if (!upstreamBase) {
    return jsonError(request, 500, "upstream_not_configured");
  }

  const token = env.ORIGENLAB_API_AUTH_TOKEN?.trim();
  if (!token) {
    return jsonError(request, 500, "auth_token_not_configured");
  }

  // Every branch above that did not already return guarantees upstreamPath
  // is non-null (POST: validated by isAllowedPostPath; GET/HEAD: the
  // explicit null check above).
  const upstreamUrl = buildUpstreamUrl(upstreamBase, upstreamPath as string, url.search);
  // Sanctioned POST commands may carry a body. Buffered rather than
  // streamed: bodies are bounded by browser/upstream API contracts, and
  // buffering keeps forwarding deterministic and testable without
  // relying on streaming-request-body support.
  const body = method === "POST" ? await request.arrayBuffer() : undefined;
  if (
    body !== undefined &&
    isAllowedMarketingCommandPostPath(upstreamPath as string) &&
    body.byteLength > marketingCommandMaxBytes(upstreamPath as string)
  ) {
    // A body larger than its declared Content-Length, or one sent without it.
    return jsonError(request, 413, "payload_too_large");
  }
  const upstreamRequest = new Request(upstreamUrl, {
    method,
    headers: buildUpstreamHeaders(env, request.headers, upstreamPath as string),
    redirect: "manual",
    body,
  });

  const upstreamResponse = await fetch(upstreamRequest);

  if (upstreamResponse.status >= 300 && upstreamResponse.status < 400) {
    const location = upstreamResponse.headers.get("Location");
    if (
      method === "GET" &&
      isAllowedAuthRedirect(upstreamPath as string, location, url.origin)
    ) {
      return authRedirectResponse(upstreamResponse, location as string);
    }
    return blockedRedirectResponse(request, method, upstreamResponse.status);
  }

  const responseHeaders = new Headers(upstreamResponse.headers);
  responseHeaders.delete(API_AUTH_HEADER);
  responseHeaders.delete("Set-Cookie");
  responseHeaders.delete("Set-Cookie2");
  if (isAuthPath(upstreamPath as string)) {
    for (const cookie of filterAuthSetCookies(upstreamResponse.headers)) {
      responseHeaders.append("Set-Cookie", cookie);
    }
  }
  stripUpstreamCorsHeaders(responseHeaders);

  const requestId = upstreamResponse.headers.get("X-Request-ID");
  if (requestId) {
    responseHeaders.set("X-Request-ID", requestId);
  }

  applyCorsHeaders(request, responseHeaders);
  applyProxyDiagnosticHeaders(responseHeaders, upstreamResponse.status);

  return new Response(method === "HEAD" ? null : upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: responseHeaders,
  });
}

export default {
  fetch(request: Request, env: ProxyEnv): Promise<Response> {
    return handleRequest(request, env);
  },
};
