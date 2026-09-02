import { describe, expect, it } from "vitest";
import { OperatorApiError } from "./operatorClient";
import { describeCustomerQuoteCommandError } from "./customerQuoteErrors";

describe("describeCustomerQuoteCommandError", () => {
  it("maps duplicate_document_number reason code to a specific Spanish message", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: "duplicate_document_number: document_number is already used by another quote" }),
      409,
    );
    expect(describeCustomerQuoteCommandError(err, "adopt")).toMatch(/número de documento/i);
  });

  it("maps duplicate_quote_number reason code distinctly from document_number", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: "duplicate_quote_number: quote_number is already used by another quote" }),
      409,
    );
    const msg = describeCustomerQuoteCommandError(err, "adopt");
    expect(msg).toMatch(/número de cotización/i);
    expect(msg).not.toMatch(/documento/i);
  });

  it("maps drive_folder_already_incorporated distinctly", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: "drive_folder_already_incorporated: this Drive folder is already attached to a durable quote" }),
      409,
    );
    expect(describeCustomerQuoteCommandError(err, "adopt")).toMatch(/ya (fue|está) incorporad/i);
  });

  it("maps sales_opportunity_not_found without leaking the raw internal id string", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: "sales_opportunity_not_found: Sales opportunity not found: sales_deadbeef" }),
      404,
    );
    const msg = describeCustomerQuoteCommandError(err, "adopt");
    expect(msg).toMatch(/oportunidad/i);
    expect(msg).not.toMatch(/sales_deadbeef/);
  });

  it("maps customer_quote_illegal_transition to an actionable conflict message", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: "customer_quote_illegal_transition: cannot close from 'approved'" }),
      409,
    );
    expect(describeCustomerQuoteCommandError(err, "close")).toMatch(/estado actual/i);
  });

  it("maps customer_quote_version_conflict to a reload-and-retry message", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: "customer_quote_version_conflict: expected 4, found 5" }),
      409,
    );
    expect(describeCustomerQuoteCommandError(err, "close")).toMatch(/cambió|recarga/i);
  });

  it("falls back to a status-based message for an unrecognized reason code, without leaking the raw code", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: "some_future_reason: whatever internal detail" }),
      409,
    );
    const msg = describeCustomerQuoteCommandError(err, "adopt");
    expect(msg).not.toMatch(/some_future_reason/);
    expect(msg.length).toBeGreaterThan(0);
  });

  it("404 falls back to a not-found message when there is no recognized reason code", () => {
    const err = new OperatorApiError(JSON.stringify({ detail: "unexpected shape" }), 404);
    expect(describeCustomerQuoteCommandError(err, "adopt")).toMatch(/no encontramos/i);
  });

  it("422 falls back to an invalid-data message", () => {
    const err = new OperatorApiError(
      JSON.stringify({ detail: [{ loc: ["body", "outcome"], msg: "value error" }] }),
      422,
    );
    expect(describeCustomerQuoteCommandError(err, "close")).toMatch(/inválid|revísa/i);
  });

  it("401 maps to an authorization message", () => {
    const err = new OperatorApiError("Authenticated commercial operator identity required", 401);
    expect(describeCustomerQuoteCommandError(err, "adopt")).toMatch(/autoriza|sesión/i);
  });

  it("503 maps to a temporarily-unavailable message regardless of exact detail text", () => {
    const err = new OperatorApiError("Commercial operations writes are disabled", 503);
    expect(describeCustomerQuoteCommandError(err, "adopt")).toMatch(/no disponible|temporal/i);
  });

  it("5xx maps to a generic unexpected-error message", () => {
    const err = new OperatorApiError("Internal Server Error", 500);
    expect(describeCustomerQuoteCommandError(err, "adopt")).toMatch(/inesperado/i);
  });

  it("malformed/non-JSON error body never throws and never echoes raw markup", () => {
    const err = new OperatorApiError("<html><body>not json</body></html>", 500);
    expect(() => describeCustomerQuoteCommandError(err, "adopt")).not.toThrow();
    expect(describeCustomerQuoteCommandError(err, "adopt")).not.toMatch(/<html>/);
  });

  it("a plain string detail (not JSON) with a reason-code prefix is still recognized", () => {
    const err = new OperatorApiError("duplicate_quote_number: quote_number already used", 409);
    expect(describeCustomerQuoteCommandError(err, "adopt")).toMatch(/número de cotización/i);
  });

  it("non-OperatorApiError (network failure) gets a connectivity message", () => {
    expect(describeCustomerQuoteCommandError(new TypeError("Failed to fetch"), "adopt")).toMatch(/conexión|conectar/i);
  });
});
