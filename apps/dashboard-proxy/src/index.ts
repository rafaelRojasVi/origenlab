import { isAllowedUpstreamPath, stripApiPrefix } from "./allowlist";
import { applyCorsHeaders, stripUpstreamCorsHeaders } from "./cors";
import { API_AUTH_HEADER, buildUpstreamHeaders, buildUpstreamUrl, type ProxyEnv } from "./proxy";

export type { ProxyEnv } from "./proxy";
export { stripApiPrefix, isAllowedUpstreamPath, buildUpstreamUrl, buildUpstreamHeaders, API_AUTH_HEADER };
export { ALLOWED_ORIGINS, applyCorsHeaders, isAllowedOrigin } from "./cors";

function jsonError(request: Request, status: number, code: string): Response {
  const headers = new Headers({ "Content-Type": "application/json" });
  applyCorsHeaders(request, headers);
  return new Response(JSON.stringify({ error: { code } }), {
    status,
    headers,
  });
}

const ALLOWED_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function handleRequest(request: Request, env: ProxyEnv): Promise<Response> {
  const url = new URL(request.url);
  const method = request.method.toUpperCase();

  if (method === "OPTIONS") {
    const headers = new Headers();
    applyCorsHeaders(request, headers);
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

  const responseHeaders = new Headers(upstreamResponse.headers);
  responseHeaders.delete(API_AUTH_HEADER);
  stripUpstreamCorsHeaders(responseHeaders);

  const requestId = upstreamResponse.headers.get("X-Request-ID");
  if (requestId) {
    responseHeaders.set("X-Request-ID", requestId);
  }

  applyCorsHeaders(request, responseHeaders);

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
