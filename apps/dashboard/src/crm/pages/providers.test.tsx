/**
 * Proveedores — the machine-candidate counts and the expanded list must read the same field
 * of the same `/v2/workspace/providers` response. The API reports a candidate's state under
 * `review.state`; the header once counted a `resolution` field the API no longer sends and
 * showed «Sin revisar 0» over a list of 172 unreviewed rows.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { ProvidersResponse } from "../crmTypes";
import { candidateState, countUnreviewed } from "../supplierCandidates";
import { ProvidersPage } from "./ProvidersPage";

/** Exactly the shape `V2CrmWorkspaceRepository.providers()` returns: `review.state`, no `resolution`. */
const CANONICAL: ProvidersResponse = {
  directory: [],
  on_cases: [],
  candidates: [
    { assertion_id: "a0000000-0000-4000-8000-000000000001", domain: "hielscher.com", trade_name: "Hielscher", review: { state: "unresolved", decided_at: null, note: null } },
    { assertion_id: "a0000000-0000-4000-8000-000000000002", domain: "ika.com", trade_name: "IKA", review: { state: "unresolved", decided_at: null, note: null } },
    { assertion_id: "a0000000-0000-4000-8000-000000000003", domain: "serva.de", trade_name: null, review: { state: "confirmed", decided_at: "2026-09-30T10:00:00+00:00", note: "es proveedor" } },
  ],
};

function withViewer(node: ReactNode) {
  const session = {
    kind: "signed_in",
    method: "google_session",
    operator: { operatorId: "o1", email: "op@example.test", displayName: "Operador", role: "viewer" },
  } as AuthSessionState;
  return <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>{node}</AuthSessionContext.Provider>;
}

function stubProviders(body: ProvidersResponse) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (url.pathname.endsWith("/v2/workspace/providers")) return json(body);
      if (url.pathname.endsWith("/auth/session")) return json({ authenticated: false, google_login_enabled: true, workspace_domain: null });
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The `<div>` of one StatLine item, found by its `<dt>` label (the label also appears on rows). */
function stat(label: string): HTMLElement {
  return screen.getByText(label, { selector: "dt" }).closest("div")!;
}

describe("supplier candidate state", () => {
  it("reads review.state first and falls back to the older resolution field", () => {
    expect(candidateState({ domain: "x", trade_name: null, review: { state: "rejected" } })).toBe("rejected");
    expect(candidateState({ domain: "x", trade_name: null, resolution: "unresolved" })).toBe("unresolved");
    expect(candidateState({ domain: "x", trade_name: null })).toBe("unresolved");
  });

  it("counts the unreviewed candidates of the canonical response", () => {
    expect(countUnreviewed(CANONICAL.candidates)).toBe(2);
  });
});

describe("ProvidersPage candidate counts", () => {
  it("«Sin revisar» equals the unreviewed rows of the expanded list, and the badge equals the list length", async () => {
    stubProviders(CANONICAL);
    render(withViewer(<ProvidersPage />));

    const summary = await screen.findByText("Candidatos por revisar");
    const statLine = stat("Sin revisar");
    expect(within(statLine).getByRole("definition")).toHaveTextContent("2");
    expect(within(stat("Candidatos")).getByRole("definition")).toHaveTextContent("3");
    expect(within(summary.parentElement!).getByText("3")).toBeInTheDocument();

    fireEvent.click(summary);
    const list = await screen.findByRole("list", { name: "Candidatos detectados" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(CANONICAL.candidates.length);
    const unreviewedRows = rows.filter((r) => within(r).queryByText("Sin revisar"));
    expect(unreviewedRows).toHaveLength(2);
    expect(within(statLine).getByRole("definition")).toHaveTextContent(String(unreviewedRows.length));
    // The reviewed row states its decision, not «Sin revisar».
    expect(within(list).getByText("confirmed")).toBeInTheDocument();
  });

  it("with no unreviewed candidate the header reads 0 and the list still shows every row", async () => {
    const reviewed: ProvidersResponse = {
      ...CANONICAL,
      candidates: CANONICAL.candidates.map((c) => ({ ...c, review: { state: "rejected", decided_at: "2026-09-30T10:00:00+00:00", note: "no" } })),
    };
    stubProviders(reviewed);
    render(withViewer(<ProvidersPage />));
    const summary = await screen.findByText("Candidatos por revisar");
    expect(within(stat("Sin revisar")).getByRole("definition")).toHaveTextContent("0");
    fireEvent.click(summary);
    const list = await screen.findByRole("list", { name: "Candidatos detectados" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
  });
});
