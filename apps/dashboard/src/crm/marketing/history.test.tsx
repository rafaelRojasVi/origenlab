import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CampaignSummary, CampaignTotals } from "../crmTypes";
import { MarketingPage } from "../pages/MarketingPage";
import { cardTotals, neverSent } from "./campaignTotals";
import type { AuditResponse, HistoryRecipient, RecipientPage, RepliesResponse } from "./marketingTypes";

// Every campaign, address and figure below is invented; the repository is public.
const ID = "b0000000-0000-4000-8000-000000000001";
const TOTALS: CampaignTotals = {
  audience: 120, included: 110, sent: 104, excluded: 10, blocked: 7, unsent: 6, rejected: 3, bounced: 9, responses: 0,
};
const HISTORICAL: CampaignSummary = {
  campaign_id: ID,
  name: "Sonicadores (histórica ficticia)",
  status: "archived",
  subject: null,
  approved_at: null,
  created_at: "2026-09-01T12:00:00Z",
  first_sent_at: "2026-09-02T13:00:00Z",
  last_sent_at: "2026-09-03T19:00:00Z",
  recipients_by_state: { sent: 95, bounced: 9, excluded: 10, snapshotted: 6 },
  send_attempts: [{ submission_state: "accepted", delivery_state: "pending", count: 104 }],

  send_batches: [],
  origin: "imported_v1",
  totals: TOTALS,
  attempt_totals: {
    attempts: 107, accepted: 104, rejected: 3, other: 0, delivery_confirmed: 0, delivery_pending: 104, delivery_bounced: 0,
    rejected_undated: 3, with_provider_id: 0, first_accepted_at: "2026-09-02T13:00:00Z", last_accepted_at: "2026-09-03T19:00:00Z",
  },
  replies: { state: "not_synced", label: "Respuestas no sincronizadas desde Gmail", count: null },
  subject_state: "not_imported",
};

function row(i: number, extra: Partial<HistoryRecipient> = {}): HistoryRecipient {
  return {
    recipient_id: `r-${i}`, address: `persona${i}@lab.example`, identity: "historical_address", person_id: null, person_name: null,
    organization_id: null, organization_name: null, state: "sent", outcome: "sent", exclusion_reasons: [], attempts: 1,
    accepted_attempts: 1, rejected_attempts: 0, delivery_confirmed: false, sent_at: "2026-09-02T13:08:06Z",
    first_sent_at: "2026-09-02T13:08:06Z", rejection: null, bounce: null, bounce_class: null, gmail_url: null, replies: 0,
    last_reply_at: null, baja: null, interests: [], ...extra,
  };
}

function page(url: URL, masked: boolean): RecipientPage {
  const total = (url.searchParams.get("total") ?? "audience") as keyof CampaignTotals;
  const n = TOTALS[total];
  const p = Number(url.searchParams.get("page") ?? "1");
  const size = Number(url.searchParams.get("page_size") ?? "50");
  const rows = Array.from({ length: Math.max(0, Math.min(size, n - (p - 1) * size)) }, (_, i) => {
    const r = row((p - 1) * size + i);
    return masked ? { ...r, address: "***@lab.example" } : r;
  });
  rows[0] &&= { ...rows[0], identity: "crm_person", person_name: "Ana Ficticia", organization_name: "Universidad Ficticia", baja: "registered" };
  return {
    campaign_id: ID, name: HISTORICAL.name, status: "archived", rows, page: p, page_size: size, total_rows: n,
    pages: Math.max(1, Math.ceil(n / size)), totals: TOTALS,
    filters: { total, reason: null, identity: null, q: url.searchParams.get("q"), search_scope: masked ? "names_only" : "address_and_names" },
    interests_available: true, storage: { table: "outbound.campaign_recipient", database: "origenlab_clean" },
  };
}

const REPLIES: RepliesResponse = {
  campaign_id: ID, name: HISTORICAL.name, status: "archived", items: [], counts: { reply: 0, baja: 0, baja_pending_review: 0 },
  sync: { state: "not_synced", label: "Respuestas no sincronizadas desde Gmail", count: null },
  baja_by_address: { recipients: 2, label: "Destinatarios con una BAJA registrada para su dirección." },
  unassociated: { baja_without_campaign_lineage: 5, label: "BAJA sin vínculo con un envío" },
  excerpts: "Los mensajes no guardan cuerpo en el CRM.", gmail_called: false,
  storage: { table: "outbound.campaign_reply", database: "origenlab_clean" },
};

const AUDIT: AuditResponse = {
  campaign_id: ID, name: HISTORICAL.name, status: "archived", version: 1,
  row: { created_at: "2026-09-23T02:07:22Z", updated_at: "2026-09-23T02:07:22Z", approved_at: null, content_frozen_at: null, audience_frozen_at: null },
  origin: { kind: "imported_v1", source_record_id: "s1", source_kind: "migration_manifest", dedupe_key: "k", manifest_name: "manifest.json",
    payload_sha256: "e".repeat(64), acquired_at: "2026-09-23T02:07:22Z", review_status: "pending" },
  events: [], attempt_events: [], events_note: "El importador histórico no registra eventos de dominio.",
  immutable: true, immutable_enforced_by_database: true, immutable_note: "Campaña archivada: la base de datos rechaza editarla.",
  actions: [], storage: { tables: ["outbound.campaign"], database: "origenlab_clean" },
};

function withRole(role: string, node: ReactNode) {
  const session = { kind: "signed_in", method: "google", operator: { email: "op@example.test", displayName: "Op", role, operatorId: "o1" } } as never;
  return <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>{node}</AuthSessionContext.Provider>;
}

function stub({
  role = "sales",
  database = "origenlab_clean",
  replies = REPLIES,
  campaign = HISTORICAL,
}: { role?: string; database?: string; replies?: RepliesResponse; campaign?: CampaignSummary } = {}) {
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      calls.push(url);
      const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (url.pathname.endsWith("/v2/workspace/marketing")) {
        return json({ campaigns: [campaign], contact_controls: [], authoring: {}, storage: { table: "outbound.campaign", database } });
      }
      if (url.pathname.endsWith("/v2/workspace/marketing/taxonomy")) return json(taxonomyJson);
      if (url.pathname.endsWith("/marketing/analytics")) return json(ANALYTICS);
      if (url.pathname.endsWith("/marketing/analytics/people")) return json(PEOPLE);
      if (url.pathname.endsWith("/history/recipients")) return json(page(url, role === "viewer"));
      if (url.pathname.endsWith("/history/replies")) return json(replies);
      if (url.pathname.endsWith("/history/audit")) return json(AUDIT);
      if (url.pathname.endsWith("/archive")) {
        return json({
          campaign_id: ID, name: HISTORICAL.name, status: "archived", subject: null, preheader: null, version: 1, content_sha256: null,
          content_frozen_at: null, audience_frozen_at: null, audience_sha256: null, audience_policy_version: null, created_at: null,
          origin: "imported_v1", sender_address: null, sender_name: null, html: null, html_state: "not_archived",
          recipients_by_state: campaign.recipients_by_state, send_attempts: campaign.send_attempts, send_batches: [],
          metrics: { opens: null, clicks: null, note: "" }, storage: { table: "outbound.campaign", database },
          totals: campaign.totals, attempt_totals: campaign.attempt_totals, replies: campaign.replies, subject_state: "not_imported",
          preheader_state: "not_imported", immutable: true, immutable_enforced_by_database: true,
        });
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return calls;
}

const ANALYTICS = {
  reply_window_days: 14,
  overview: { reached: 104, writable: 100, bounced: 3, baja: 1, other_blocks: 0, with_open_case: 2 },
  campaigns: { [ID]: { sent: 104, new_people: 104, bounced: 3, baja: 1, responded: 1, quote_request: 1, cases_after: 0, ndr_unblocked: 0 } },
  coverage: [],
  alerts: { ndr_unblocked: 0 },
  mail_captured_from: "2026-08-01T00:00:00+00:00",
};
const PEOPLE = {
  total: 1, limit: 50, offset: 0,
  rows: [{ address_norm: "ana@lab.example", opportunity_id: null, person_id: null, name: "Ana Ficticia", organization: null, case_stage: null, case_title: null }],
};

/** A sent campaign is a row now; its totals live in the detail the row opens. */
async function openSentCampaign() {
  fireEvent.click(await screen.findByTestId("campaign-row"));
  await screen.findByTestId("campaign-detail");
}

async function openTotal(key: string) {
  await openSentCampaign();
  fireEvent.click(await screen.findByTestId(`detail-total-${key}`));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("campaign card totals", () => {
  it("shows six totals and never a zero for replies that were never synchronized", () => {
    const t = cardTotals(HISTORICAL);
    expect(t.map((x) => x.key)).toEqual(["audience", "sent", "excluded", "rejected", "bounced", "responses"]);
    expect(t.find((x) => x.key === "responses")?.value).toBeNull();
    expect(t.find((x) => x.key === "sent")?.value).toBe(104);
  });

  it("every detail total opens the recipient rows filtered by that same total, and they reconcile", async () => {
    for (const key of ["audience", "sent", "excluded", "rejected", "bounced"] as const) {
      const calls = stub();
      const view = render(withRole("sales", <MarketingPage />));
      await openSentCampaign();
      const button = await screen.findByTestId(`detail-total-${key}`);
      expect(button).toHaveTextContent(new Intl.NumberFormat("es-CL").format(TOTALS[key]));
      fireEvent.click(button);
      const range = await screen.findByTestId("recipients-range");
      expect(range).toHaveTextContent(`de ${new Intl.NumberFormat("es-CL").format(TOTALS[key])} destinatarios`);
      expect(calls.some((u) => u.pathname.endsWith("/history/recipients") && u.searchParams.get("total") === key)).toBe(true);
      expect(screen.getByTestId(`filter-${key}`)).toHaveAttribute("aria-pressed", "true");
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("the replies tab lists who wrote back, measured, and never says «No sincronizadas»", async () => {
    stub();
    render(withRole("sales", <MarketingPage />));
    await openSentCampaign();
    expect(screen.queryByTestId("detail-total-responses")).toBeNull();
    expect(await screen.findByTestId("result-responded")).toHaveTextContent("1");
    fireEvent.click(await screen.findByRole("button", { name: "Respuestas" }));
    expect(await screen.findByTestId("people-list")).toHaveTextContent("Ana Ficticia");
    expect(screen.queryByText(/No sincronizadas/)).toBeNull();
    expect(screen.getByTestId("baja-by-address")).toHaveTextContent("2");
    expect(screen.getByTestId("baja-unassociated")).toHaveTextContent("5");
  });

  it("a historical card offers no editor, only its history", async () => {
    stub();
    render(withRole("admin", <MarketingPage />));
    const row = await screen.findByTestId("campaign-row");
    expect(within(row).queryByRole("button", { name: "Editar" })).toBeNull();
    expect(within(row).queryByRole("button", { name: "Abrir" })).toBeNull();
    fireEvent.click(row);
    expect(await screen.findByText("Cerrada · solo lectura")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Editar|Abrir campaña|Reenviar|Reabrir/ })).toBeNull();
  });
});

// An archived campaign whose whole audience was excluded before any send: zero attempts.
const NEVER_SENT: CampaignSummary = {
  ...HISTORICAL,
  name: "Centrífugas (nunca enviada, ficticia)",
  first_sent_at: null,
  last_sent_at: null,
  recipients_by_state: { excluded: 12 },
  send_attempts: [],
  send_batches: [],
  totals: { audience: 12, included: 0, sent: 0, excluded: 12, blocked: 0, unsent: 0, rejected: 0, bounced: 0, responses: 0 },
  attempt_totals: {
    attempts: 0, accepted: 0, rejected: 0, other: 0, delivery_confirmed: 0, delivery_pending: 0, delivery_bounced: 0,
    rejected_undated: 0, with_provider_id: 0, first_accepted_at: null, last_accepted_at: null,
  },
};

describe("an archived campaign that was never sent", () => {
  it("is «Nunca enviada» only when archived with zero attempts", () => {
    expect(neverSent(NEVER_SENT)).toBe(true);
    expect(neverSent(HISTORICAL)).toBe(false);
    expect(neverSent({ ...NEVER_SENT, status: "draft" })).toBe(false);
    expect(neverSent({ ...NEVER_SENT, attempt_totals: null })).toBe(true);
    expect(neverSent({ ...NEVER_SENT, attempt_totals: null, send_attempts: [{ submission_state: "rejected", delivery_state: "none", count: 1 }] })).toBe(false);
  });

  it("the card, the detail header and the send record say «Nunca enviada»", async () => {
    stub({ campaign: NEVER_SENT });
    render(withRole("admin", <MarketingPage />));
    // Hidden from the list by default (owner, 2026-10-04); the link shows it.
    fireEvent.click(await screen.findByRole("button", { name: "Mostrar 1 campaña nunca enviada" }));
    const card = await screen.findByTestId("campaign-card");
    expect(within(card).getByTestId("never-sent")).toHaveTextContent("Nunca enviada");
    fireEvent.click(within(card).getByTestId("open-history"));
    expect(await screen.findByTestId("detail-never-sent")).toHaveTextContent("Nunca enviada");
    expect(await screen.findByTestId("send-dates")).toHaveTextContent("Nunca enviada");
    expect(screen.queryByRole("button", { name: /Editar|Abrir campaña|Reenviar|Reabrir|Planificar/ })).toBeNull();
  });

  it("a sent historical campaign never carries the label", async () => {
    stub();
    render(withRole("admin", <MarketingPage />));
    const row = await screen.findByTestId("campaign-row");
    expect(within(row).queryByTestId("never-sent")).toBeNull();
    fireEvent.click(row);
    await screen.findByText("Cerrada · solo lectura");
    expect(screen.queryByText("Nunca enviada")).toBeNull();
  });
});

describe("recipients tab", () => {
  it("pages on the server and keeps the filter", async () => {
    const calls = stub();
    render(withRole("sales", <MarketingPage />));
    await openTotal("sent");
    expect(await screen.findByTestId("recipients-range")).toHaveTextContent("1–50 de 104");
    fireEvent.click(screen.getByTestId("recipients-next"));
    await waitFor(() => expect(screen.getByTestId("recipients-range")).toHaveTextContent("51–100 de 104"));
    const last = calls.filter((u) => u.pathname.endsWith("/history/recipients")).at(-1)!;
    expect(last.searchParams.get("page")).toBe("2");
    expect(last.searchParams.get("total")).toBe("sent");
  });

  it("labels a CRM person, a historical address, a missing institution and a registered BAJA", async () => {
    stub();
    render(withRole("sales", <MarketingPage />));
    await openTotal("audience");
    const table = await screen.findByTestId("recipients-table");
    expect(table).toHaveTextContent("Persona del CRM");
    expect(table).toHaveTextContent("Dirección histórica");
    expect(table).toHaveTextContent("Sin institución vinculada");
    expect(table).toHaveTextContent("Universidad Ficticia");
    expect(table).toHaveTextContent("BAJA registrada");
    fireEvent.click(within(table).getAllByTestId("recipient-row")[1]);
    const drawer = await screen.findByRole("dialog");
    expect(drawer).toHaveTextContent("Sin confirmación de entrega");
    expect(drawer).toHaveTextContent("Sin interés registrado");
  });

  it("a viewer reads masked addresses and is told the search covers names only", async () => {
    stub({ role: "viewer" });
    render(withRole("viewer", <MarketingPage />));
    await openTotal("audience");
    const table = await screen.findByTestId("recipients-table");
    expect(table).not.toHaveTextContent(/persona\d+@/);
    expect(table).toHaveTextContent("***@lab.example");
    expect(screen.getByTestId("viewer-search-note")).toHaveTextContent("sólo cubre nombres");
    expect(screen.getByRole("searchbox", { name: "Buscar destinatarios" })).toHaveAttribute("placeholder", "Buscar por persona o institución del CRM");
  });
});

describe("replies and audit", () => {
  it("keeps stored replies, lineage BAJA and pending review apart", async () => {
    stub({
      replies: {
        ...REPLIES,
        sync: { state: "partial", label: "Respuestas registradas por lotes.", count: 1 },
        counts: { reply: 1, baja: 1, baja_pending_review: 1 },
        items: [
          { id: "1", kind: "reply", class: "human_reply", class_label: "Respuesta", classified_by: "ingest_classifier", received_at: "2026-09-04T10:00:00Z",
            association: "recipient", recipient_id: "r-1", address: "a@lab.example", person_name: null, organization_name: null, excerpt: null,
            gmail_url: "https://mail.google.com/mail/u/0/#all/gm-1" },
          { id: "2", kind: "baja", class: "unsubscribe_request", class_label: "BAJA / REMOVER", classified_by: "baja_grammar", received_at: null,
            association: "send_lineage", recipient_id: "r-2", address: "b@lab.example", person_name: null, organization_name: null, excerpt: null, gmail_url: null },
          { id: "3", kind: "baja_pending_review", class: "unsubscribe_request", class_label: "BAJA en revisión", classified_by: "baja_grammar",
            received_at: null, association: "address_match", recipient_id: "r-3", address: "c@lab.example", person_name: null, organization_name: null,
            excerpt: null, gmail_url: null },
        ],
      },
    });
    render(withRole("sales", <MarketingPage />));
    await openSentCampaign();
    fireEvent.click(await screen.findByRole("button", { name: "Respuestas" }));
    const items = await screen.findAllByTestId("reply-item");
    expect(items.map((i) => i.getAttribute("data-kind"))).toEqual(["reply", "baja", "baja_pending_review"]);
    expect(items[2]).toHaveTextContent("Asociada sólo por dirección");
    expect(within(items[0]).getByRole("link")).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/gm-1");
    expect(screen.queryByTestId("replies-not-synced")).toBeNull();
  });

  it("the audit tab says the history is immutable and offers no action", async () => {
    stub();
    render(withRole("admin", <MarketingPage />));
    await openSentCampaign();
    fireEvent.click(await screen.findByRole("button", { name: "Auditoría" }));
    expect(await screen.findByTestId("audit-immutable")).toHaveTextContent("Inmutable");
    expect(screen.getByTestId("audit-no-actions")).toBeInTheDocument();
    expect(screen.getByTestId("audit-no-events")).toHaveTextContent("no registra eventos");
  });
});

describe("data source", () => {
  it("warns loudly when the campaigns come from a disposable test database", async () => {
    stub({ database: "origenlab_test_abcd1234" });
    render(withRole("sales", <MarketingPage />));
    expect(await screen.findByTestId("fixture-warning")).toHaveTextContent("datos inventados");
  });

  it("says nothing of the kind for the clean room", async () => {
    stub();
    render(withRole("sales", <MarketingPage />));
    await screen.findByTestId("campaign-row");
    expect(screen.queryByTestId("fixture-warning")).toBeNull();
  });
});
