import { afterEach, describe, expect, it, vi } from "vitest";
import { OperatorApiError, SESSION_REFUSED_EVENT } from "../../api/operatorClient";
import {
  QUOTE_COMMAND_PATHS,
  QuoteRefusal,
  approveQuoteRevision,
  createQuote,
  createQuoteRevision,
  fetchQuoteNumbering,
  fetchQuoteWorkspace,
  newQuoteCommandKey,
  previewQuote,
  saveQuoteDraft,
  voidQuoteRevision,
  type QuoteDraft,
} from "./quoteApi";

const QUOTE_ID = "11111111-1111-4111-8111-111111111111";
const REVISION_ID = "22222222-2222-4222-8222-222222222222";
const CASE_ID = "33333333-3333-4333-8333-333333333333";

const DRAFT: QuoteDraft = {
  items: [
    {
      description: "Balanza de ejemplo",
      quantity: "2",
      margin_mode: "markup",
      margin_pct: "0.30",
      unit_cost: "100.00",
      cost_currency: "EUR",
      is_principal: true,
    },
  ],
  manual_expenses: [],
  expense_method: "profile",
  allocation_mode: "principal",
  principal_rule: "highest_cost_imported_equipment",
  fx_overrides: [],
  terms: { terms_extra: [], validity_days: 15 },
  recipient: { participant_id: "44444444-4444-4444-8444-444444444444" },
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stub(respond: (path: string, init: RequestInit) => Response = () => json({})) {
  const calls: { path: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init: RequestInit = {}) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      calls.push({ path, init });
      return Promise.resolve(respond(path, init));
    }),
  );
  return calls;
}

const headerOf = (init: RequestInit, name: string) => new Headers(init.headers as HeadersInit).get(name);
const bodyOf = (init: RequestInit) => JSON.parse(String(init.body));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("quote reads", () => {
  it("GETs the workspace with the session cookie", async () => {
    const calls = stub(() => json({ quote: { id: QUOTE_ID } }));
    await fetchQuoteWorkspace(QUOTE_ID);
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe(`/v2/quotes/${QUOTE_ID}`);
    expect(calls[0].init.method).toBe("GET");
    expect(calls[0].init.credentials).toBe("include");
    expect(headerOf(calls[0].init, "Idempotency-Key")).toBeNull();
  });

  it("GETs the numbering status", async () => {
    const calls = stub(() =>
      json({ configured: true, seed: 42, next_number: "00042-26", highest_known: 41, ignored_mail_numbers: [] }),
    );
    const numbering = await fetchQuoteNumbering();
    expect(calls[0].path).toBe("/v2/quotes/numbering");
    expect(calls[0].init.method).toBe("GET");
    expect(numbering.next_number).toBe("00042-26");
  });
});

describe("preview", () => {
  it("POSTs revision and draft without an Idempotency-Key and passes the abort signal", async () => {
    const calls = stub(() => json({ computed: { lines: [] } }));
    const controller = new AbortController();
    await previewQuote(REVISION_ID, DRAFT, controller.signal);
    const { path, init } = calls[0];
    expect(path).toBe("/v2/quotes/preview");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(headerOf(init, "Idempotency-Key")).toBeNull();
    expect(headerOf(init, "Content-Type")).toBe("application/json");
    expect(init.signal).toBe(controller.signal);
    expect(bodyOf(init)).toEqual({ revision_id: REVISION_ID, draft: DRAFT });
  });
});

describe("commands", () => {
  const receipt = { revision_id: REVISION_ID, version: 2, computed: {} };

  it("pins the five command paths", () => {
    expect(QUOTE_COMMAND_PATHS).toEqual({
      create: "/v2/commands/create-quote",
      save: "/v2/commands/save-quote-draft",
      approve: "/v2/commands/approve-quote-revision",
      void: "/v2/commands/void-quote-revision",
      revise: "/v2/commands/create-quote-revision",
    });
  });

  it("createQuote sends the case and the given key", async () => {
    const calls = stub(() => json({ quote_id: QUOTE_ID, quote_number: "00042-26", revision_id: REVISION_ID, version: 1 }));
    const out = await createQuote(CASE_ID, "key-1");
    expect(calls[0].path).toBe("/v2/commands/create-quote");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.credentials).toBe("include");
    expect(headerOf(calls[0].init, "Idempotency-Key")).toBe("key-1");
    expect(bodyOf(calls[0].init)).toEqual({ opportunity_id: CASE_ID });
    expect(out.quote_number).toBe("00042-26");
  });

  it("saveQuoteDraft sends revision, version and draft", async () => {
    const calls = stub(() => json(receipt));
    await saveQuoteDraft(REVISION_ID, 1, DRAFT, "key-2");
    expect(calls[0].path).toBe("/v2/commands/save-quote-draft");
    expect(headerOf(calls[0].init, "Idempotency-Key")).toBe("key-2");
    expect(bodyOf(calls[0].init)).toEqual({ revision_id: REVISION_ID, expected_version: 1, draft: DRAFT });
  });

  it("approveQuoteRevision sends revision and version", async () => {
    const calls = stub(() => json({ version: 3 }));
    await approveQuoteRevision(REVISION_ID, 2, "key-3");
    expect(calls[0].path).toBe("/v2/commands/approve-quote-revision");
    expect(headerOf(calls[0].init, "Idempotency-Key")).toBe("key-3");
    expect(bodyOf(calls[0].init)).toEqual({ revision_id: REVISION_ID, expected_version: 2 });
  });

  it("voidQuoteRevision adds the reason", async () => {
    const calls = stub(() => json({ version: 4 }));
    await voidQuoteRevision(REVISION_ID, 3, "Error de precio", "key-4");
    expect(calls[0].path).toBe("/v2/commands/void-quote-revision");
    expect(headerOf(calls[0].init, "Idempotency-Key")).toBe("key-4");
    expect(bodyOf(calls[0].init)).toEqual({ revision_id: REVISION_ID, expected_version: 3, reason: "Error de precio" });
  });

  it("createQuoteRevision sends the quote", async () => {
    const calls = stub(() => json({ revision_id: REVISION_ID, revision_no: 2 }));
    await createQuoteRevision(QUOTE_ID, "key-5");
    expect(calls[0].path).toBe("/v2/commands/create-quote-revision");
    expect(headerOf(calls[0].init, "Idempotency-Key")).toBe("key-5");
    expect(bodyOf(calls[0].init)).toEqual({ quote_id: QUOTE_ID });
  });

  it("generates a key when none is given", async () => {
    const calls = stub(() => json({ version: 3 }));
    await approveQuoteRevision(REVISION_ID, 2);
    const key = headerOf(calls[0].init, "Idempotency-Key");
    expect(key).toBeTruthy();
    expect(key!.length).toBeGreaterThan(8);
  });

  it("newQuoteCommandKey returns a fresh value each call", () => {
    expect(newQuoteCommandKey()).not.toBe(newQuoteCommandKey());
  });
});

describe("refusals", () => {
  it("parses the API envelope {detail:{code,message}}", async () => {
    stub(() => json({ detail: { code: "stale_version", message: "version changed" } }, 409));
    const err = await saveQuoteDraft(REVISION_ID, 1, DRAFT, "k").catch((e) => e);
    expect(err).toBeInstanceOf(QuoteRefusal);
    expect(err.status).toBe(409);
    expect(err.code).toBe("stale_version");
    expect(err.message).toBe("version changed");
  });

  it("parses the Worker envelope {error:{code,message}}", async () => {
    stub(() => json({ error: { code: "path_not_allowed", message: "no" } }, 403));
    const err = await createQuote(CASE_ID, "k").catch((e) => e);
    expect(err).toBeInstanceOf(QuoteRefusal);
    expect(err.status).toBe(403);
    expect(err.code).toBe("path_not_allowed");
  });

  it("maps a viewer's 403 on a read to role_may_not_view_costs", async () => {
    stub(() => json({ detail: { code: "role_may_not_view_costs", message: "sales or admin only" } }, 403));
    const err = await fetchQuoteWorkspace(QUOTE_ID).catch((e) => e);
    expect(err).toBeInstanceOf(QuoteRefusal);
    expect(err).toBeInstanceOf(OperatorApiError);
    expect(err.status).toBe(403);
    expect(err.code).toBe("role_may_not_view_costs");
  });

  it("refuses a preview with a code too", async () => {
    stub(() => json({ detail: { code: "labdelivery_refused", message: "x" } }, 422));
    const err = await previewQuote(REVISION_ID, DRAFT).catch((e) => e);
    expect(err).toBeInstanceOf(QuoteRefusal);
    expect(err.code).toBe("labdelivery_refused");
  });

  it("keeps the API's validation errors of a 422 for the page to show", async () => {
    const errors = [{ loc: ["body", "draft", "items", "1", "description"], msg: "String should have at least 1 character", type: "string_too_short" }];
    stub(() => json({ error: { code: "validation_error", message: "Request validation failed", details: { validation_errors: errors } } }, 422));
    const err = await saveQuoteDraft(REVISION_ID, 1, DRAFT, "k").catch((e) => e);
    expect(err).toBeInstanceOf(QuoteRefusal);
    expect(err.code).toBe("validation_error");
    expect(err.details).toEqual({ validation_errors: errors });
    expect(err.validationErrors).toEqual(errors);
  });

  it("has no validation errors when the refusal carries none", async () => {
    stub(() => json({ detail: { code: "stale_version", message: "x" } }, 409));
    const err = await saveQuoteDraft(REVISION_ID, 1, DRAFT, "k").catch((e) => e);
    expect(err.details).toBeNull();
    expect(err.validationErrors).toEqual([]);
  });

  it("keeps a null code for a body that is not an envelope", async () => {
    stub(() => new Response("upstream down", { status: 502 }));
    const err = await fetchQuoteNumbering().catch((e) => e);
    expect(err).toBeInstanceOf(QuoteRefusal);
    expect(err.status).toBe(502);
    expect(err.code).toBeNull();
  });

  it("announces a refused session on 401", async () => {
    stub(() => json({ detail: { code: "profile_required", message: "x" } }, 401));
    const heard = vi.fn();
    window.addEventListener(SESSION_REFUSED_EVENT, heard);
    await fetchQuoteWorkspace(QUOTE_ID).catch(() => undefined);
    window.removeEventListener(SESSION_REFUSED_EVENT, heard);
    expect(heard).toHaveBeenCalledTimes(1);
  });
});
