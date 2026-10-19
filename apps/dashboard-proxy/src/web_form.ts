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
  // I3: only the one path this handler owns
  const url = new URL(request.url);
  if (url.pathname !== "/api/web/solicitud") {
    return new Response(null, { status: 404 });
  }

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

  // Parse body (JSON, application/x-www-form-urlencoded, or multipart/form-data)
  let fields: Record<string, string> = {};
  const ct = (request.headers.get("Content-Type") ?? "").split(";")[0].trim().toLowerCase();
  if (ct === "application/json") {
    try {
      fields = JSON.parse(new TextDecoder().decode(buf));
    } catch {
      // Invalid JSON — still forward, let upstream validate
    }
  } else if (ct === "application/x-www-form-urlencoded") {
    fields = Object.fromEntries(new URLSearchParams(new TextDecoder().decode(buf)));
  } else if (ct.startsWith("multipart/form-data")) {
    // Parse multipart/form-data using FormData via Response trick
    const respClone = new Response(buf, { headers: { "Content-Type": request.headers.get("Content-Type") ?? "" } });
    try {
      const fd = await respClone.formData();
      for (const [k, v] of fd.entries()) {
        if (typeof v === "string") fields[k] = v;
      }
    } catch { /* skip, fields stays as {} */ }
  }

  // Detect no-JS path: if Content-Type is form-encoded or multipart, the browser submitted
  // the form natively (JS path always sends JSON after the C2 fix).
  const isNoJs = ct === "application/x-www-form-urlencoded" || ct.startsWith("multipart/form-data");

  // C2: generate submission_id for no-JS requests that don't include one
  if (!fields["submission_id"]) {
    fields["submission_id"] = crypto.randomUUID();
  }

  // Honeypot: if _trap field is non-empty, return silent success without forwarding
  if (fields["_trap"]) {
    return new Response(JSON.stringify({ status: "received" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Timing gate: reject submissions faster than 3 seconds after page render.
  // render_at may be a float-seconds string (JS path, post-C2) or an ISO string (legacy no-JS).
  const renderAtRaw = fields["render_at"] ?? "0";
  let renderAt = Number(renderAtRaw);
  if (isNaN(renderAt)) {
    renderAt = Date.parse(renderAtRaw) / 1000;
  }
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

  // C2: always forward as JSON regardless of the original Content-Type
  const upstreamResp = await fetch(
    new Request(upstreamUrl, { method: "POST", headers: upstreamHeaders, body: JSON.stringify(fields) }),
  );

  // C2: no-JS browsers (form-encoded or multipart submission) get a 303 redirect on success
  if (isNoJs && upstreamResp.ok) {
    return new Response(null, { status: 303, headers: { Location: "/contacto/?ok=1" } });
  }
  return new Response(upstreamResp.body, {
    status: upstreamResp.status,
    headers: { "Content-Type": "application/json" },
  });
}
