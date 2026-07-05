import { isAllowedUpstreamPath, stripApiPrefix } from "./allowlist";
import { applyCorsHeaders, stripUpstreamCorsHeaders } from "./cors";
import { API_AUTH_HEADER, buildUpstreamHeaders, buildUpstreamUrl, type ProxyEnv } from "./proxy";

export type { ProxyEnv } from "./proxy";
export { stripApiPrefix, isAllowedUpstreamPath, buildUpstreamUrl, buildUpstreamHeaders, API_AUTH_HEADER };
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

  if (MUTATING_METHODS.has(method)) {
    return jsonError(request, 405, "method_not_allowed");
  }

  if (!ALLOWED_METHODS.has(method)) {
    return jsonError(request, 405, "method_not_allowed");
  }

  const upstreamPath = stripApiPrefix(url.pathname);
  if (upstreamPath === null) {
    return jsonError(request, 404, "not_found");
  }

  if (!isAllowedUpstreamPath(upstreamPath)) {
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

  const upstreamUrl = buildUpstreamUrl(upstreamBase, upstreamPath, url.search);
  const upstreamRequest = new Request(upstreamUrl, {
    method,
    headers: buildUpstreamHeaders(env, request.headers),
    redirect: "manual",
  });

  const upstreamResponse = await fetch(upstreamRequest);

  if (upstreamResponse.status >= 300 && upstreamResponse.status < 400) {
    return blockedRedirectResponse(request, method, upstreamResponse.status);
  }

  const responseHeaders = new Headers(upstreamResponse.headers);
  responseHeaders.delete(API_AUTH_HEADER);
  responseHeaders.delete("Set-Cookie");
  responseHeaders.delete("Set-Cookie2");
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
