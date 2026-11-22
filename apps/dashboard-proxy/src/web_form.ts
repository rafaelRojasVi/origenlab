import {
  API_AUTH_HEADER,
  CF_ACCESS_CLIENT_ID_HEADER,
  CF_ACCESS_CLIENT_SECRET_HEADER,
  buildUpstreamUrl,
  type ProxyEnv,
} from "./proxy";

export const WEB_FORM_UPSTREAM_PATH = "/v2/web/quote-request";
const WEB_FORM_ORIGIN = "https://origenlab.cl";
const WEB_FORM_HOSTNAME = "origenlab.cl";
const WEB_FORM_BODY_LIMIT = 16_384;

/** Cloudflare Turnstile's server-side check (developers.cloudflare.com/turnstile). */
export const TURNSTILE_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
/** The form field the widget fills inside the `<form>` (implicit rendering). */
export const TURNSTILE_TOKEN_FIELD = "cf-turnstile-response";
/** The widget's `data-action` on both site forms (`apps/web/src/components/TurnstileWidget.astro`). */
export const TURNSTILE_ACTION = "solicitud";
const TURNSTILE_TIMEOUT_MS = 5_000;
const FALLBACK_MESSAGE = "Escríbanos directamente a contacto@origenlab.cl";

interface RateLimit {
  limit(opts: { key: string }): Promise<{ success: boolean }>;
}

export type WebFormEnv = ProxyEnv & {
  WEB_FORM_RATE_LIMIT?: RateLimit;
  /**
   * Turnstile secret key (a Worker secret). Set = every submission needs a token the widget
   * issued, checked with siteverify before anything is forwarded. Unset = no challenge.
   */
  TURNSTILE_SECRET_KEY?: string;
};

function jsonRefusal(status: number, code: string): Response {
  return new Response(JSON.stringify({ error: { code, message: FALLBACK_MESSAGE } }), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/** Seconds since the epoch from a float-seconds string or number, or an ISO string; null when unusable. */
function parseRenderAt(raw: unknown): number | null {
  let value: number;
  if (typeof raw === "number") {
    value = raw;
  } else if (typeof raw === "string" && raw.trim() !== "") {
    value = Number(raw);
    if (Number.isNaN(value)) {
      value = Date.parse(raw) / 1000;
    }
  } else {
    return null;
  }
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** Whether siteverify accepts this token for this site; null when siteverify could not answer. */
async function turnstileAccepts(secret: string, token: string, ip: string | null): Promise<boolean | null> {
  const form = new URLSearchParams({ secret, response: token });
  if (ip) form.set("remoteip", ip);
  let resp: Response;
  try {
    resp = await fetch(TURNSTILE_SITEVERIFY_URL, {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(TURNSTILE_TIMEOUT_MS),
    });
  } catch {
    return null;
  }
  if (!resp.ok) return null;
  let outcome: { success?: unknown; hostname?: unknown; action?: unknown };
  try {
    outcome = (await resp.json()) as typeof outcome;
  } catch {
    return null;
  }
  return outcome.success === true && outcome.hostname === WEB_FORM_HOSTNAME && outcome.action === TURNSTILE_ACTION;
}

export async function handleWebFormRequest(request: Request, env: WebFormEnv): Promise<Response> {
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
  let fields: Record<string, unknown> = {};
  const ct = (request.headers.get("Content-Type") ?? "").split(";")[0].trim().toLowerCase();
  if (ct === "application/json") {
    try {
      const parsed: unknown = JSON.parse(new TextDecoder().decode(buf));
      // Only a plain object is a submission: null, a string, a number or an array is treated
      // as an empty one (it is refused below for want of render_at), never dereferenced.
      if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
        fields = parsed as Record<string, unknown>;
      }
    } catch {
      // Invalid JSON: an empty submission, refused below for want of render_at.
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
  // (Without JavaScript render_at stays empty, so such a post is refused below; only a
  // form-encoded post that carries one gets the 303 to /contacto/enviado/.)

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

  // Timing gate. Both site forms set render_at from JavaScript when the page renders, so a
  // submission without a usable one did not come from them: refused, never forwarded.
  // render_at may be a float-seconds number or string, or an ISO string.
  const renderAt = parseRenderAt(fields["render_at"]);
  if (renderAt === null) {
    return jsonRefusal(400, "render_at_required");
  }
  fields["render_at"] = renderAt;
  // Faster than 3 seconds after render: a bot. Silent success, nothing forwarded.
  if (Date.now() / 1000 - renderAt < 3) {
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

  // Turnstile, only once the owner has set the secret: a token the widget issued for this site
  // and this form, checked server-side before anything is forwarded. Fail closed.
  const turnstileSecret = env.TURNSTILE_SECRET_KEY?.trim();
  if (turnstileSecret) {
    const token = fields[TURNSTILE_TOKEN_FIELD];
    if (typeof token !== "string" || token === "" || token.length > 2048) {
      return jsonRefusal(403, "challenge_required");
    }
    const accepted = await turnstileAccepts(turnstileSecret, token, request.headers.get("CF-Connecting-IP"));
    if (accepted === null) {
      return jsonRefusal(503, "challenge_unavailable");
    }
    if (!accepted) {
      return jsonRefusal(403, "challenge_failed");
    }
  }
  // Neither the token nor the honeypot means anything to the API.
  delete fields[TURNSTILE_TOKEN_FIELD];
  delete fields["_trap"];

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
    return new Response(null, { status: 303, headers: { Location: `${WEB_FORM_ORIGIN}/contacto/enviado/` } });
  }
  return new Response(upstreamResp.body, {
    status: upstreamResp.status,
    headers: { "Content-Type": "application/json" },
  });
}
