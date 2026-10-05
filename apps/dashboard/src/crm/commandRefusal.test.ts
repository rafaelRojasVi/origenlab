import { describe, expect, it } from "vitest";
import { OperatorApiError } from "../api/operatorClient";
import {
  REFUSAL_MESSAGES,
  isNotEnabledRefusal,
  isStaleRefusal,
  parseRefusal,
  refusalFromError,
  refusalText,
} from "./commandRefusal";

/** What `apps/api/src/origenlab_api/errors.py` answers for a `CommandRefused` in production. */
function productionBody(code: string, generic = "conflict", extra: Record<string, unknown> = {}) {
  return {
    error: {
      code: generic,
      message: "the person was modified since you loaded it",
      details: { code, message: "the person was modified since you loaded it", ...extra },
      request_id: "req-1",
    },
  };
}

/** What a bare FastAPI app (no error handlers) answers for the same refusal. */
function bareBody(code: string, extra: Record<string, unknown> = {}) {
  return { detail: { code, message: "the person was modified since you loaded it", ...extra } };
}

describe("parseRefusal reads every envelope", () => {
  it("the production envelope: the specific code from details, not the generic one", () => {
    const r = parseRefusal(409, productionBody("stale_version"));
    expect(r).toEqual({
      status: 409,
      code: "stale_version",
      message: "the person was modified since you loaded it",
      details: { code: "stale_version", message: "the person was modified since you loaded it" },
    });
  });

  it("the bare FastAPI envelope", () => {
    const r = parseRefusal(409, bareBody("stale_version"));
    expect(r.code).toBe("stale_version");
    expect(r.message).toBe("the person was modified since you loaded it");
  });

  it("the same refusal reads the same from both envelopes, given as raw response text too", () => {
    for (const body of [productionBody("record_busy"), bareBody("record_busy")]) {
      expect(parseRefusal(409, body).code).toBe("record_busy");
      expect(parseRefusal(409, JSON.stringify(body)).code).toBe("record_busy");
    }
  });

  it("a top-level code with no details (the API's own record_busy/service_busy/duplicate)", () => {
    const body = { error: { code: "duplicate", message: "a record with these values already exists", details: { constraint: "contact_point_kind_value_key" }, request_id: "r" } };
    const r = parseRefusal(409, body);
    expect(r.code).toBe("duplicate");
    expect(r.details).toEqual({ constraint: "contact_point_kind_value_key" });
  });

  it("extra detail fields survive in details (e.g. the test-send limit's next_allowed_at)", () => {
    const at = "2026-10-05T13:00:00Z";
    expect(parseRefusal(429, productionBody("test_send_limit", "validation_error", { next_allowed_at: at })).details.next_allowed_at).toBe(at);
    expect(parseRefusal(429, bareBody("test_send_limit", { next_allowed_at: at })).details.next_allowed_at).toBe(at);
  });

  it("the Worker's own refusal", () => {
    expect(parseRefusal(403, { error: { code: "path_not_allowed" } }).code).toBe("path_not_allowed");
    expect(parseRefusal(403, "path_not_allowed").code).toBe("path_not_allowed");
  });

  it("a string detail, a validation list, an HTML page and an empty body fall back to the status", () => {
    expect(parseRefusal(404, { detail: "Not Found" })).toMatchObject({ code: "http_404", message: "Not Found" });
    expect(parseRefusal(422, { detail: [{ loc: ["body", "note"], msg: "field required" }] }).code).toBe("validation_error");
    expect(parseRefusal(502, "<html><body>Bad gateway</body></html>")).toEqual({ status: 502, code: "http_502", message: "", details: {} });
    expect(parseRefusal(500, "")).toMatchObject({ code: "http_500" });
    expect(parseRefusal(500, null)).toMatchObject({ code: "http_500" });
  });

  it("an error with no answer at all is network_error", () => {
    expect(refusalFromError(new TypeError("Failed to fetch"))).toMatchObject({ status: 0, code: "network_error" });
    expect(refusalFromError(new OperatorApiError(JSON.stringify(productionBody("stale_version")), 409)).code).toBe("stale_version");
  });
});

describe("refusalText: one Spanish table, never a raw body", () => {
  const STALE = "Otro operador modificó este registro mientras lo editabas. Carga la versión actual; tus cambios siguen en el formulario.";

  it.each([
    ["stale_version", STALE],
    ["case_version_conflict", STALE],
    ["record_busy", "Otro operador está guardando este registro. Reintenta en unos segundos."],
    ["service_busy", "El sistema está ocupado. Reintenta en unos segundos."],
    ["duplicate", "Ya existe un registro con esos datos."],
  ])("%s, from the production envelope", (code, text) => {
    expect(refusalText(parseRefusal(409, productionBody(code)))).toBe(text);
    expect(refusalText(parseRefusal(409, bareBody(code)))).toBe(text);
  });

  it.each(["idempotency_key_reused", "command_in_progress", "idempotency_conflict", "command_already_failed", "network_error"])(
    "%s has a Spanish sentence of its own",
    (code) => {
      const text = refusalText({ status: 409, code, message: "english", details: {} });
      expect(text).toBe(REFUSAL_MESSAGES[code]);
      expect(text).not.toMatch(/english|code|Idempotency/i);
    },
  );

  it("every mapped code is Spanish prose: no JSON, no code, no English API text", () => {
    for (const [code, text] of Object.entries(REFUSAL_MESSAGES)) {
      expect(text, code).not.toMatch(/[{}"]/);
      expect(text, code).not.toContain(code);
    }
  });

  it("an unknown code falls back to Spanish with the code for support, never the body", () => {
    const body = productionBody("something_new");
    const text = refusalText(parseRefusal(409, body));
    expect(text).toBe("No se pudo completar la acción (código something_new).");
    expect(text).not.toContain("{");
    expect(text).not.toContain("modified");
    expect(refusalText(parseRefusal(409, body), { fallback: "No se pudo crear la persona" })).toBe(
      "No se pudo crear la persona (código something_new).",
    );
  });

  it("a server failure without a code is said as one, whatever the page behind it", () => {
    expect(refusalText(new OperatorApiError("<html>502 Bad Gateway</html>", 502))).toBe(REFUSAL_MESSAGES.internal_error);
    expect(refusalText(new OperatorApiError(JSON.stringify({ error: { code: "internal_error", message: "An unexpected error occurred" } }), 500))).toBe(
      REFUSAL_MESSAGES.internal_error,
    );
  });

  it("a screen may word a known code its own way", () => {
    const r = parseRefusal(409, productionBody("stale_version"));
    expect(refusalText(r, { overrides: { stale_version: "Otra frase." } })).toBe("Otra frase.");
  });

  it("accepts the raw error as well as a parsed refusal", () => {
    expect(refusalText(new TypeError("Failed to fetch"))).toBe(REFUSAL_MESSAGES.network_error);
    expect(refusalText(new OperatorApiError(JSON.stringify(productionBody("record_busy")), 409))).toBe(REFUSAL_MESSAGES.record_busy);
  });
});

describe("classifiers", () => {
  it("stale: a moved version, in either name", () => {
    expect(isStaleRefusal(parseRefusal(409, productionBody("stale_version")))).toBe(true);
    expect(isStaleRefusal(parseRefusal(409, bareBody("case_version_conflict")))).toBe(true);
    expect(isStaleRefusal(parseRefusal(409, productionBody("record_busy")))).toBe(false);
    expect(isStaleRefusal(null)).toBe(false);
  });

  it("not enabled: an unmounted route or the Worker, not a missing record", () => {
    expect(isNotEnabledRefusal(parseRefusal(404, { error: { code: "not_found", message: "Not Found", details: {} } }))).toBe(true);
    expect(isNotEnabledRefusal(parseRefusal(404, { detail: "Not Found" }))).toBe(true);
    expect(isNotEnabledRefusal(parseRefusal(403, { error: { code: "path_not_allowed" } }))).toBe(true);
    expect(isNotEnabledRefusal(parseRefusal(405, { detail: "Method Not Allowed" }))).toBe(true);
    expect(isNotEnabledRefusal(parseRefusal(404, productionBody("campaign_not_found", "not_found")))).toBe(false);
  });
});
