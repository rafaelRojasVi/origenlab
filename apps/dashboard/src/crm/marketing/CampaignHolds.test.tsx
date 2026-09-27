import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CampaignHoldPanel, HoldsBanner } from "./CampaignHolds";
import { CAMPAIGN_COMMAND_PATHS } from "./marketingApi";
import type { CampaignBlock, CampaignHoldsResponse } from "./marketingTypes";

// Every campaign, operator and reason below is invented; the repository is public.
const CAMPAIGN = "c0000000-0000-4000-8000-0000000000b1";

const SEPTEMBER: CampaignBlock = {
  block_id: "b0000000-0000-4000-8000-000000000001", scope: "legacy_campaign", scope_label: "Campaña del registro V1",
  campaign_id: null, legacy_campaign_key: "septiembre18-2026-wave2", reason: "Retención por incidente del 2026-09-21",
  reference: "incident_hold_september_2026", placed_at: "2026-09-27T23:45:00Z", placed_by_kind: "migrator",
  placed_by: "Migración", lifted_at: null, lifted_by: null, lift_reason: null, version: 1, active: true,
};

function holds(overrides: Partial<CampaignHoldsResponse> = {}): CampaignHoldsResponse {
  return {
    all_campaigns: { blocked: false, block: null, block_version: 0 },
    legacy: [SEPTEMBER],
    active: [SEPTEMBER],
    recently_lifted: [],
    by_campaign: { [CAMPAIGN]: { held: false, refusals: [], block: null, block_version: 2 } },
    effect: "x",
    expires: false,
    commands_enabled: true,
    may_decide: true,
    ...overrides,
  };
}

function redacted(b: CampaignBlock): CampaignBlock {
  const { reason: _r, placed_by: _p, lifted_by: _l, lift_reason: _lr, ...rest } = b;
  return { ...rest, redacted: true };
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function stub(reads: CampaignHoldsResponse[]) {
  const posts: { path: string; body: Record<string, unknown>; key: string | null }[] = [];
  let n = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      if ((init?.method ?? "GET") === "POST") {
        const headers = new Headers(init?.headers);
        posts.push({ path: url.pathname, body: JSON.parse(String(init?.body)), key: headers.get("Idempotency-Key") });
        return jsonResponse({ block: SEPTEMBER, block_version: 1, campaign_refusals: [], effect: "x", expires: false, enqueues: false, sends: false });
      }
      if (url.pathname.endsWith("/v2/workspace/marketing/campaign-blocks")) {
        const body = reads[Math.min(n, reads.length - 1)];
        n += 1;
        return jsonResponse(body);
      }
      return jsonResponse({ detail: "not found" }, 404);
    }),
  );
  return posts;
}

describe("campaign safety blocks", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows a viewer the September hold as status only, with no action", async () => {
    const posts = stub([holds({ legacy: [redacted(SEPTEMBER)], active: [redacted(SEPTEMBER)], may_decide: false })]);
    render(<HoldsBanner />);
    const banner = await screen.findByTestId("holds-banner");
    const legacy = within(banner).getByTestId("legacy-hold");
    expect(legacy).toHaveTextContent("septiembre18-2026-wave2");
    expect(legacy).toHaveTextContent("incident_hold_september_2026");
    expect(legacy).toHaveTextContent("sólo es visible para Ventas y Administración");
    expect(banner).not.toHaveTextContent("Retención por incidente");
    expect(within(banner).queryAllByRole("button")).toHaveLength(0);
    expect(posts).toEqual([]);
  });

  it("shows sales why a campaign is held, but no block or lift action", async () => {
    const block: CampaignBlock = { ...SEPTEMBER, block_id: "b2", scope: "campaign", scope_label: "Esta campaña",
      campaign_id: CAMPAIGN, legacy_campaign_key: null, reference: null, reason: "Revisión de contenido", placed_by: "Admin", placed_by_kind: "operator" };
    stub([holds({ may_decide: false, by_campaign: { [CAMPAIGN]: { held: true, refusals: [{ code: "campaign_blocked", label: "Campaña bloqueada" }], block, block_version: 1 } } })]);
    render(<CampaignHoldPanel campaignId={CAMPAIGN} />);
    const hold = await screen.findByTestId("campaign-hold");
    expect(hold).toHaveAttribute("data-held", "true");
    expect(hold).toHaveTextContent("Campaña bloqueada");
    expect(screen.getByTestId("block-reason")).toHaveTextContent("Revisión de contenido");
    expect(screen.getByTestId("hold-read-only")).toHaveTextContent("Sólo un administrador");
    expect(screen.queryByRole("button", { name: /bloquear|levantar/i })).toBeNull();
  });

  it("lets an admin block a campaign only with a reason and a confirmation, quoting the block version", async () => {
    const placed: CampaignBlock = { ...SEPTEMBER, block_id: "b3", scope: "campaign", scope_label: "Esta campaña",
      campaign_id: CAMPAIGN, legacy_campaign_key: null, reference: null, reason: "Incidente", placed_by_kind: "operator", placed_by: "Admin" };
    const posts = stub([
      holds(),
      holds({ by_campaign: { [CAMPAIGN]: { held: true, refusals: [{ code: "campaign_blocked", label: "Campaña bloqueada" }], block: placed, block_version: 3 } } }),
    ]);
    const onChanged = vi.fn();
    render(<CampaignHoldPanel campaignId={CAMPAIGN} onChanged={onChanged} />);
    fireEvent.click(await screen.findByTestId("block-campaign-open"));
    const submit = screen.getByTestId("block-campaign-submit");
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId("block-campaign-reason"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("block-campaign-confirm"));
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId("block-campaign-reason"), { target: { value: " Incidente en revisión " } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe(CAMPAIGN_COMMAND_PATHS.block);
    expect(posts[0].body).toEqual({ scope: "campaign", campaign_id: CAMPAIGN, expected_block_version: 2, reason: "Incidente en revisión" });
    expect(posts[0].key).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("campaign-hold")).toHaveAttribute("data-held", "true"));
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByTestId(`unblock-b3-open`)).toBeInTheDocument();
  });

  it("lets an admin lift the September hold with its own reason and the block's version", async () => {
    const posts = stub([holds(), holds({ legacy: [], active: [] })]);
    render(<HoldsBanner />);
    const open = await screen.findByTestId(`unblock-${SEPTEMBER.block_id}-open`);
    fireEvent.click(open);
    fireEvent.change(screen.getByTestId(`unblock-${SEPTEMBER.block_id}-reason`), { target: { value: "Revisión del propietario cerrada" } });
    fireEvent.click(screen.getByTestId(`unblock-${SEPTEMBER.block_id}-confirm`));
    fireEvent.click(screen.getByTestId(`unblock-${SEPTEMBER.block_id}-submit`));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe(CAMPAIGN_COMMAND_PATHS.unblock);
    expect(posts[0].body).toEqual({ block_id: SEPTEMBER.block_id, expected_version: 1, reason: "Revisión del propietario cerrada" });
    await waitFor(() => expect(screen.queryByTestId("legacy-hold")).toBeNull());
  });

  it("offers an admin the all-campaigns block, and nothing that sends", async () => {
    const posts = stub([holds({ legacy: [], active: [] })]);
    render(<HoldsBanner />);
    fireEvent.click(await screen.findByTestId("block-all-open"));
    fireEvent.change(screen.getByTestId("block-all-reason"), { target: { value: "Pausa general" } });
    fireEvent.click(screen.getByTestId("block-all-confirm"));
    fireEvent.click(screen.getByTestId("block-all-submit"));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({ scope: "all_campaigns", campaign_id: null, expected_block_version: 0, reason: "Pausa general" });
    expect(screen.queryByRole("button", { name: /enviar|aprobar|activar|reanudar/i })).toBeNull();
  });

  it("renders nothing for a viewer when nothing is held", async () => {
    stub([holds({ legacy: [], active: [], may_decide: false })]);
    const { container } = render(<HoldsBanner />);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
