import {
  API_AUTH_HEADER,
  CF_ACCESS_CLIENT_ID_HEADER,
  CF_ACCESS_CLIENT_SECRET_HEADER,
  buildUpstreamUrl,
  type ProxyEnv,
} from "./proxy";

export const WEB_FORM_UPSTREAM_PATH = "/v2/web/quote-request";
const WEB_FORM_ORIGIN = "https://origenlab.cl";
const WEB_FORM_BODY_LIMIT = 16_384;

interface RateLimit {
  limit(opts: { key: string }): Promise<{ success: boolean }>;
}

export async function handleWebFormRequest(
  request: Request,
  env: ProxyEnv & { WEB_FORM_RATE_LIMIT?: RateLimit },
): Promise<Response> {
  if (request.method !== "POST") {
    return new Response(null, { status: 405 });
  }
  if (request.headers.get("Origin") !== WEB_FORM_ORIGIN) {
    return new Response(null, { status: 403 });
  }

  // Check Content-Length header first (fast path)
  const contentLengthRaw = request.headers.get("Content-Length");
  if (contentLengthRaw !== null) {
    const contentLength = Number(contentLengthRaw);
    if (!Number.isFinite(contentLength) || contentLength > WEB_FORM_BODY_LIMIT) {
      return new Response(null, { status: 413 });
    }
  }

  const buf = await request.arrayBuffer();
  if (buf.byteLength > WEB_FORM_BODY_LIMIT) {
    return new Response(null, { status: 413 });
  }

  // Parse body (JSON or application/x-www-form-urlencoded)
  let fields: Record<string, string> = {};
  const ct = (request.headers.get("Content-Type") ?? "").split(";")[0].trim().toLowerCase();
  if (ct === "application/json") {
    try {
      fields = JSON.parse(new TextDecoder().decode(buf));
    } catch {
      // Invalid JSON — still forward, let upstream validate
    }
  } else {
    fields = Object.fromEntries(new URLSearchParams(new TextDecoder().decode(buf)));
  }

  // Honeypot: if _trap field is non-empty, return silent success without forwarding
  if (fields["_trap"]) {
    return new Response(JSON.stringify({ status: "received" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Timing gate: reject submissions faster than 3 seconds after page render
  const renderAt = Number(fields["render_at"] ?? "0");
  if (Number.isFinite(renderAt) && renderAt > 0 && Date.now() / 1000 - renderAt < 3) {
    return new Response(JSON.stringify({ status: "received" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Rate limit (optional binding — if absent, WAF handles it)
  if (env.WEB_FORM_RATE_LIMIT) {
    const { success } = await env.WEB_FORM_RATE_LIMIT.limit({
      key: request.headers.get("CF-Connecting-IP") ?? "unknown",
    });
    if (!success) {
      return new Response(
        JSON.stringify({
          error: {
            code: "rate_limited",
            message: "Escríbanos directamente a contacto@origenlab.cl",
          },
        }),
        { status: 429, headers: { "Content-Type": "application/json" } },
      );
    }
  }

  // Forward to upstream API — no session cookie, no operator header, no CORS headers
  const upstreamUrl = buildUpstreamUrl(env.ORIGENLAB_API_UPSTREAM, WEB_FORM_UPSTREAM_PATH, "");
  const upstreamHeaders = new Headers({ "Content-Type": "application/json" });

  const token = env.ORIGENLAB_API_AUTH_TOKEN?.trim();
  if (token) {
    upstreamHeaders.set(API_AUTH_HEADER, token);
  }
  const cfId = env.CF_ACCESS_CLIENT_ID?.trim();
  const cfSecret = env.CF_ACCESS_CLIENT_SECRET?.trim();
  if (cfId && cfSecret) {
    upstreamHeaders.set(CF_ACCESS_CLIENT_ID_HEADER, cfId);
    upstreamHeaders.set(CF_ACCESS_CLIENT_SECRET_HEADER, cfSecret);
  }

  const upstreamResp = await fetch(
    new Request(upstreamUrl, { method: "POST", headers: upstreamHeaders, body: buf }),
  );
  return new Response(upstreamResp.body, {
    status: upstreamResp.status,
    headers: { "Content-Type": "application/json" },
  });
}
