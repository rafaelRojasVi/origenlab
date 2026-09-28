import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CampaignSummary, CampaignTotals } from "../crmTypes";
import { MarketingPage } from "../pages/MarketingPage";
import { AudienceFreeze } from "./AudienceFreeze";
import { CampaignEditor } from "./CampaignEditor";
import { mayAuthorCampaigns } from "./authoring";
import type { CampaignContent, EquipmentTaxonomy } from "./marketingTypes";

// Every campaign, address and figure below is invented; the repository is public.
const taxonomy = taxonomyJson as EquipmentTaxonomy;
const TOTALS: CampaignTotals = { audience: 3, included: 2, sent: 2, excluded: 1, blocked: 0, unsent: 0, rejected: 0, bounced: 0, responses: 0 };
const REPLIES = { state: "not_synced", label: "Respuestas no sincronizadas desde Gmail", count: null } as CampaignSummary["replies"];

const ARCHIVED: CampaignSummary = {
  campaign_id: "a0000000-0000-4000-8000-000000000001", name: "Histórica ficticia", status: "archived", subject: null,
  approved_at: null, created_at: "2026-09-01T12:00:00Z", first_sent_at: "2026-09-02T13:00:00Z", last_sent_at: "2026-09-02T14:00:00Z",
  recipients_by_state: { sent: 2, excluded: 1 }, send_attempts: [{ submission_state: "accepted", delivery_state: "pending", count: 2 }],
  replies_recorded: 0, send_batches: [], origin: "imported_v1", totals: TOTALS, replies: REPLIES, subject_state: "not_imported",
  attempt_totals: {
    attempts: 2, accepted: 2, rejected: 0, other: 0, delivery_confirmed: 0, delivery_pending: 2, delivery_bounced: 0,
    rejected_undated: 0, with_provider_id: 0, first_accepted_at: "2026-09-02T13:00:00Z", last_accepted_at: "2026-09-02T14:00:00Z",
  },
};
const DRAFT: CampaignSummary = {
  ...ARCHIVED, campaign_id: "a0000000-0000-4000-8000-000000000002", name: "Borrador ficticio", status: "draft", origin: "native_v2",
  subject: "Asunto ficticio", first_sent_at: null, last_sent_at: null, recipients_by_state: {}, send_attempts: [], totals: null,
  attempt_totals: null, version: 2, planned_for_date: "2099-01-15", planned_for_at: null, planning_version: 1,
};
const FROZEN: CampaignSummary = { ...DRAFT, campaign_id: "a0000000-0000-4000-8000-000000000003", name: "Congelada ficticia", status: "audience_frozen" };
const CAMPAIGNS = [DRAFT, FROZEN, ARCHIVED];

const CONTENT: CampaignContent = {
  campaign_id: DRAFT.campaign_id, name: DRAFT.name, status: "draft", subject: "Asunto ficticio", preheader: null, body_html: "<p>Hola</p>",
  has_text: false, version: 2, max_sends: 50, recontact_interval_days: 90, created_at: null, updated_at: "2026-09-27T10:00:00Z",
  created_by: "Op", database: "origenlab_test_abcd1234",
};

/** Every write the page could make is a non-GET fetch; the stub records every request it sees. */
function stub() {
  const calls: { path: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      calls.push({ path: url.pathname, method: (init?.method ?? "GET").toUpperCase() });
      const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      const p = url.pathname;
      if (init?.method && init.method.toUpperCase() !== "GET") return json({});
      if (p.endsWith("/v2/workspace/marketing")) {
        return json({
          campaigns: CAMPAIGNS, contact_controls: [], replies_note: "",
          // Every write switched on: only the role may hide a control.
          authoring: { drafts_enabled: true, freeze_enabled: true, planning_enabled: true },
          storage: { table: "outbound.campaign", database: "origenlab_clean" },
        });
      }
      if (p.endsWith("/v2/workspace/marketing/taxonomy")) return json(taxonomy);
      if (p.endsWith("/history/recipients")) {
        return json({
          campaign_id: ARCHIVED.campaign_id, name: ARCHIVED.name, status: "archived", rows: [], page: 1, page_size: 50, total_rows: 0,
          pages: 1, totals: TOTALS, filters: { total: "audience", reason: null, identity: null, q: null, search_scope: "names_only" },
          interests_available: true, storage: { table: "outbound.campaign_recipient", database: "origenlab_clean" },
        });
      }
      if (p.endsWith("/history/replies")) {
        return json({
          campaign_id: ARCHIVED.campaign_id, name: ARCHIVED.name, status: "archived", items: [], counts: { reply: 0, baja: 0, baja_pending_review: 0 },
          sync: REPLIES, baja_by_address: { recipients: 0, label: "" }, unassociated: { baja_without_campaign_lineage: 0, label: "" },
          excerpts: "", gmail_called: false, storage: { table: "outbound.campaign_reply", database: "origenlab_clean" },
        });
      }
      if (p.endsWith("/history/audit")) {
        return json({
          campaign_id: ARCHIVED.campaign_id, name: ARCHIVED.name, status: "archived", version: 1,
          row: { created_at: null, updated_at: null, approved_at: null, content_frozen_at: null, audience_frozen_at: null },
          origin: { kind: "imported_v1", source_record_id: "s1", source_kind: "migration_manifest", dedupe_key: "k", manifest_name: "manifest.json",
            payload_sha256: "e".repeat(64), acquired_at: "2026-09-23T02:07:22Z", review_status: "pending" },
          events: [], attempt_events: [], events_note: "", immutable: true, immutable_enforced_by_database: true,
          immutable_note: "", actions: [], storage: { tables: ["outbound.campaign"], database: "origenlab_clean" },
        });
      }
      if (p.endsWith("/archive")) {
        const c = CAMPAIGNS.find((x) => p.includes(x.campaign_id)) ?? ARCHIVED;
        return json({
          campaign_id: c.campaign_id, name: c.name, status: c.status, subject: null, preheader: null, version: 1, content_sha256: null,
          content_frozen_at: null, audience_frozen_at: null, audience_sha256: null, audience_policy_version: null, created_at: null,
          origin: c.origin, sender_address: null, sender_name: null, html: null, html_state: c.status === "archived" ? "not_archived" : "not_frozen",
          recipients_by_state: c.recipients_by_state, send_attempts: c.send_attempts, send_batches: [],
          metrics: { opens: null, clicks: null, note: "" }, storage: { table: "outbound.campaign", database: "origenlab_clean" },
          totals: c.totals, attempt_totals: c.attempt_totals, replies: c.replies, subject_state: "not_imported",
          preheader_state: "not_imported", immutable: c.status === "archived", immutable_enforced_by_database: true,
        });
      }
      if (p.includes("/campaigns/")) return json({ ...CONTENT, campaign_id: p.split("/").pop(), status: p.includes(FROZEN.campaign_id) ? "audience_frozen" : "draft" });
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return calls;
}

const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET" && c.method !== "HEAD");

function sessionFor(role: string | null): AuthSessionState {
  return role === null
    ? ({ kind: "loading" } as AuthSessionState)
    : ({ kind: "signed_in", method: "google_session", operator: { operatorId: "o1", email: "op@example.test", displayName: "Op", role } } as AuthSessionState);
}

function withRole(role: string | null, node: ReactNode) {
  return <AuthSessionContext.Provider value={{ session: sessionFor(role), signOut: async () => undefined }}>{node}</AuthSessionContext.Provider>;
}

/** Any label a write affordance carries in Marketing. None may be on screen for a reader. */
const WRITE_LABEL = /^(Nueva campaña|Editar|Editar borrador|Abrir|Abrir campaña|Duplicar|Nueva versión.*|Guardar.*|Congelar.*|Quitar fecha|Quitar decisión|Confirmar.*)$/;

function expectNoWriteAffordance() {
  const offending = screen.queryAllByRole("button").filter((b) => WRITE_LABEL.test((b.textContent ?? "").trim()));
  expect(offending.map((b) => b.textContent)).toEqual([]);
  expect(screen.queryByTestId("save-planning")).toBeNull();
  expect(screen.queryByTestId("clear-planning")).toBeNull();
  expect(document.querySelector("form")).toBeNull();
  expect(document.querySelector('input[type="date"], input[type="time"], textarea')).toBeNull();
}

/** Click every enabled button inside `root`, except those that leave the current view. */
function clickEverything(root: HTMLElement) {
  for (const b of within(root).queryAllByRole("button")) {
    if (/Volver/.test(b.textContent ?? "") || (b as HTMLButtonElement).disabled) continue;
    fireEvent.click(b);
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("mayAuthorCampaigns", () => {
  it.each([
    ["sales", true],
    ["admin", true],
    ["viewer", false],
    ["auditor", false],
    ["", false],
  ])("a signed-in %s → %s", (role, expected) => {
    expect(mayAuthorCampaigns(sessionFor(role))).toBe(expected);
  });

  it.each(["loading", "signed_out", "unconfigured", "error"])("a session that is «%s» may not author", (kind) => {
    expect(mayAuthorCampaigns({ kind } as AuthSessionState)).toBe(false);
  });
});

describe.each([
  ["a viewer", "viewer"],
  ["an unknown role", "auditor"],
  ["no confirmed session", null],
])("Marketing for %s: every read, no write", (_who, role) => {
  it("the list and calendar offer no «Nueva campaña», «Editar» or «Abrir» for any campaign", async () => {
    const calls = stub();
    render(withRole(role, <MarketingPage />));
    const cards = await screen.findAllByTestId("campaign-card");
    expect(cards).toHaveLength(3);
    expectNoWriteAffordance();
    // Every card still opens its history.
    for (const card of cards) expect(within(card).getByTestId("open-history")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Calendario" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Nueva campaña" })).toBeNull());
    expectNoWriteAffordance();
    expect(writes(calls)).toEqual([]);
  });

  it.each([
    ["the draft", DRAFT],
    ["the frozen campaign", FROZEN],
    ["the archived campaign", ARCHIVED],
  ])("clicking %s opens its read-only detail, never the editor or the freeze screen", async (_what, c) => {
    const calls = stub();
    render(withRole(role, <MarketingPage />));
    const cards = await screen.findAllByTestId("campaign-card");
    const card = cards.find((x) => within(x).queryByText(c.name))!;
    fireEvent.click(within(card).getByRole("button", { name: c.name }));
    const detail = await screen.findByTestId("campaign-detail");
    expect(screen.queryByTestId("persistence")).toBeNull();
    expect(screen.queryByTestId("audience-freeze")).toBeNull();
    expect(screen.queryByTestId("author-only")).toBeNull();
    expectNoWriteAffordance();
    if (c.status !== "archived") {
      // The planned day is shown, read-only, and says why it cannot be changed.
      expect(await screen.findByTestId("planning-current")).toHaveTextContent("Planificada para");
      expect(screen.getByTestId("planning-read-only")).toHaveTextContent("requiere el rol Ventas o Administración");
    }
    // Every read-only tab stays open to a reader, and nothing on any of them writes.
    for (const tab of ["Resumen", "HTML", "Destinatarios", "Respuestas", "Auditoría"]) {
      fireEvent.click(within(detail).getByRole("button", { name: new RegExp(`^${tab}`) }));
      await waitFor(() => expect(screen.getByTestId("campaign-detail")).toBeInTheDocument());
      await new Promise((r) => setTimeout(r, 0));
      clickEverything(screen.getByTestId("campaign-detail"));
      expectNoWriteAffordance();
    }
    await waitFor(() => expect(calls.some((x) => x.path.endsWith("/history/audit"))).toBe(true));
    expect(writes(calls)).toEqual([]);
  });

  it("the equipment audiences and Bajas tabs stay readable and write nothing", async () => {
    const calls = stub();
    render(withRole(role, <MarketingPage />));
    await screen.findAllByTestId("campaign-card");
    for (const tab of ["Audiencias por equipo", "Bajas"]) {
      fireEvent.click(screen.getByRole("button", { name: tab }));
      await new Promise((r) => setTimeout(r, 0));
      expectNoWriteAffordance();
    }
    expect(writes(calls)).toEqual([]);
  });

  it("the editor and the freeze screen refuse to render their controls even if reached directly", async () => {
    const calls = stub();
    const { unmount } = render(
      withRole(role, <CampaignEditor seed={{ stored: CONTENT }} taxonomy={taxonomy} draftsEnabled onSaved={() => undefined} onDuplicate={() => undefined} onFreeze={() => undefined} />),
    );
    expect(screen.getByTestId("author-only")).toHaveTextContent("requiere el rol Ventas o Administración");
    expect(screen.queryAllByRole("button")).toEqual([]);
    unmount();
    for (const status of ["draft", "audience_frozen"] as const) {
      const view = render(
        withRole(role, <AudienceFreeze campaign={{ ...CONTENT, status }} taxonomy={taxonomy} freezeEnabled onFrozen={() => undefined} onNewVersion={() => undefined} />),
      );
      expect(screen.getByTestId("author-only")).toBeInTheDocument();
      expect(screen.queryAllByRole("button")).toEqual([]);
      view.unmount();
    }
    expect(writes(calls)).toEqual([]);
  });
});

describe.each(["sales", "admin"])("Marketing for %s keeps its permitted actions", (role) => {
  it("offers «Nueva campaña», «Editar» on the draft and «Abrir» on the frozen campaign; the archived one has none", async () => {
    stub();
    render(withRole(role, <MarketingPage />));
    const cards = await screen.findAllByTestId("campaign-card");
    expect(screen.getByRole("button", { name: "Nueva campaña" })).toBeInTheDocument();
    const byName = (n: string) => cards.find((x) => within(x).queryByText(n))!;
    expect(within(byName(DRAFT.name)).getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(within(byName(FROZEN.name)).getByRole("button", { name: "Abrir" })).toBeInTheDocument();
    expect(within(byName(ARCHIVED.name)).queryByRole("button", { name: /^(Editar|Abrir)$/ })).toBeNull();
  });

  it("the draft's detail offers «Editar borrador» and the planning form; «Editar» opens the editor with «Duplicar»", async () => {
    stub();
    render(withRole(role, <MarketingPage />));
    const cards = await screen.findAllByTestId("campaign-card");
    const draft = cards.find((x) => within(x).queryByText(DRAFT.name))!;
    fireEvent.click(within(draft).getByTestId("open-history"));
    await screen.findByTestId("campaign-detail");
    expect(screen.getByRole("button", { name: "Editar borrador" })).toBeInTheDocument();
    expect(await screen.findByTestId("save-planning")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "← Volver" }));
    const again = await screen.findAllByTestId("campaign-card");
    fireEvent.click(within(again.find((x) => within(x).queryByText(DRAFT.name))!).getByRole("button", { name: "Editar" }));
    expect(await screen.findByTestId("persistence")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Duplicar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Guardar/ })).toBeInTheDocument();
    expect(screen.queryByTestId("author-only")).toBeNull();
  });

  it("the freeze screen renders for an author", async () => {
    stub();
    render(withRole(role, <AudienceFreeze campaign={{ ...CONTENT, status: "draft" }} taxonomy={taxonomy} freezeEnabled onFrozen={() => undefined} onNewVersion={() => undefined} />));
    expect(screen.queryByTestId("author-only")).toBeNull();
    expect(await screen.findByTestId("audience-freeze")).toBeInTheDocument();
  });
});
