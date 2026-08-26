export const API_AUTH_HEADER = "X-OriginLab-API-Key";
export const OPERATOR_EMAIL_HEADER = "X-OriginLab-Operator-Email";
export const IDEMPOTENCY_KEY_HEADER = "Idempotency-Key";
export const CF_ACCESS_USER_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email";
export const CF_ACCESS_CLIENT_ID_HEADER = "CF-Access-Client-Id";
export const CF_ACCESS_CLIENT_SECRET_HEADER = "CF-Access-Client-Secret";

export interface ProxyEnv {
  ORIGENLAB_API_UPSTREAM: string;
  ORIGENLAB_API_AUTH_TOKEN: string;
  CF_ACCESS_CLIENT_ID?: string;
  CF_ACCESS_CLIENT_SECRET?: string;
}

export function buildUpstreamUrl(
  upstreamBase: string,
  upstreamPath: string,
  search: string,
): string {
  const base = upstreamBase.replace(/\/$/, "");
  const path = upstreamPath.startsWith("/") ? upstreamPath : `/${upstreamPath}`;
  return `${base}${path}${search}`;
}

export function buildUpstreamHeaders(env: ProxyEnv, incoming: Headers): Headers {
  const headers = new Headers();
  headers.set("Accept", incoming.get("Accept") || "application/json");

  // Relevant for sanctioned POST commands; GET/HEAD requests normally
  // do not carry these. Forward byte-identical so the upstream API sees the
  // same Content-Type/Content-Length the browser sent.
  const contentType = incoming.get("Content-Type");
  if (contentType) {
    headers.set("Content-Type", contentType);
  }
  const contentLength = incoming.get("Content-Length");
  if (contentLength) {
    headers.set("Content-Length", contentLength);
  }

  const idempotencyKey = incoming.get(IDEMPOTENCY_KEY_HEADER);
  if (idempotencyKey) {
    headers.set(IDEMPOTENCY_KEY_HEADER, idempotencyKey);
  }

  // Never forward a browser-supplied OriginLab operator header.
  // Reconstruct it exclusively from Cloudflare Access authenticated identity.
  headers.delete(OPERATOR_EMAIL_HEADER);

  const authenticatedOperator = incoming
    .get(CF_ACCESS_USER_EMAIL_HEADER)
    ?.trim()
    .toLowerCase();

  if (authenticatedOperator) {
    headers.set(OPERATOR_EMAIL_HEADER, authenticatedOperator);
  }

  const token = env.ORIGENLAB_API_AUTH_TOKEN?.trim();
  if (token) {
    headers.set(API_AUTH_HEADER, token);
  }

  const cfId = env.CF_ACCESS_CLIENT_ID?.trim();
  const cfSecret = env.CF_ACCESS_CLIENT_SECRET?.trim();
  if (cfId && cfSecret) {
    headers.set(CF_ACCESS_CLIENT_ID_HEADER, cfId);
    headers.set(CF_ACCESS_CLIENT_SECRET_HEADER, cfSecret);
  }

  const requestId = incoming.get("X-Request-ID");
  if (requestId) {
    headers.set("X-Request-ID", requestId);
  }

  return headers;
}
