import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceOverview } from "../crmTypes";
import { ReviewPage } from "./ReviewPage";

// Every value below is invented; the repository is public.
const OVERVIEW: WorkspaceOverview = {
  entities: [
    { key: "opportunities", count: 12, provenance: "imported", note: "Casos importados." },
    { key: "drive_links_in_crm", count: 0, provenance: "not_imported", note: "Aún no se guardan en el CRM." },
  ],
  opportunities_by_stage: { quoting: 12 },
  organizations_by_confirmation: { confirmed: 3 },
  contact_points_linked: { organization: 0, person: 0 },
  assertions: [{ kind: "contact_address", resolution: "unresolved", count: 7 }],
  drive_archive: { configured: true, documents: 30, revisions_with_drive_file: 14, revisions_total: 14 },
};

function respond(routes: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      calls.push(path);
      const body = routes[path];
      if (body === undefined) return Promise.resolve(new Response("{}", { status: 404 }));
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("Revisión → Estado de los datos", () => {
  const ROUTES = {
    "/v2/cockpit/work-queue": { items: [], total: 0, limit: 200, offset: 0 },
    "/v2/workspace/review": { archived_not_in_crm: [], open_assertions: [], ambiguous_organizations: [], drive_configured: true },
    "/v2/workspace/overview": OVERVIEW,
  };

  it("reads the data-health counts only when the tab is opened", async () => {
    const calls = respond(ROUTES);
    render(<ReviewPage navigate={() => undefined} />);
    await screen.findByText("Sin bloqueos en el CRM");
    expect(calls).not.toContain("/v2/workspace/overview");

    fireEvent.click(screen.getByRole("button", { name: /Estado de los datos/ }));
    expect(await screen.findByText("Qué hay en el CRM")).toBeInTheDocument();
    expect(calls).toContain("/v2/workspace/overview");
  });

  it("says where each Drive figure comes from", async () => {
    respond(ROUTES);
    render(<ReviewPage navigate={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: /Estado de los datos/ }));
    expect(await screen.findByText("14/14")).toBeInTheDocument();
    expect(screen.getByText(/según el registro de la carga a Drive/)).toBeInTheDocument();
    expect(screen.getByText("Enlaces de Drive guardados en el CRM")).toBeInTheDocument();
  });
});

describe("Revisión → Bloqueos del CRM", () => {
  it("shows the queue's true count for a kind when the page holds only part of it", async () => {
    const evidence = [0, 1].map((i) => ({
      kind: "pending_evidence", reason: "r", next_action: "n",
      subject_ids: { source_record_id: `00000000-0000-4000-8000-00000000000${i}` }, age_days: 1, label: "gmail_message",
    }));
    respond({
      "/v2/cockpit/work-queue": { items: evidence, total: 412, counts: { pending_evidence: 412 }, limit: 200, offset: 0 },
      "/v2/workspace/review": { archived_not_in_crm: [], open_assertions: [], ambiguous_organizations: [], drive_configured: true },
    });
    render(<ReviewPage navigate={() => undefined} />);
    expect(await screen.findByText("· 412")).toBeInTheDocument();
    expect(screen.getByText(/se muestran los 2 más antiguos/)).toBeInTheDocument();
  });
});
