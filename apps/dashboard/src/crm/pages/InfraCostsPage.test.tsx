/**
 * «Costos e infraestructura» dashboard tests.
 *
 * All values are invented; the repository is public.
 */
import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InfraCostsPage } from "./InfraCostsPage";
import type { InfraPayload } from "../operations";

// ---------------------------------------------------------------------------
// Payloads (all fictitious)
// ---------------------------------------------------------------------------

const PAYLOAD_FULL: InfraPayload = {
  supabase: {
    database_bytes: 2_147_483_648, // 2 GB
    largest_tables: [
      { table: "crm.opportunity", bytes: 800_000_000 },
      { table: "evidence.source_record", bytes: 500_000_000 },
    ],
    mail_bytes: 1_073_741_824, // 1 GB
    mail_count: 10_000,
    mail_bytes_this_month: 107_374_182, // 100 MB
    plan: "Pro",
    plan_monthly_usd: 25.0,
    database_gb_included: 8.0,
    file_storage_gb_included: 100.0,
  },
  render: {
    available: true,
    services: [
      {
        id: "svc-1",
        name: "origenlab-api",
        type: "web",
        plan: "starter",
        suspended: false,
        price_usd: 7.0,
        bandwidth_bytes_this_month: 3_758_096_384, // 3.5 GB
      },
    ],
    workspace_plan: "Hobby",
    workspace_monthly_usd: 0.0,
    bandwidth_bytes_this_month: 3_758_096_384,
    bandwidth_gb_included: 5.0,
    bandwidth_overage_usd_per_100_gb: 15.0,
  },
  monthly_cost_usd: 32.0,
  plans_as_of: "2026-10-30",
};

const PAYLOAD_NO_RENDER: InfraPayload = {
  ...PAYLOAD_FULL,
  render: {
    available: false,
    reason: "Falta la clave de la API de Render en el servicio",
  },
  monthly_cost_usd: 25.0,
};

const PAYLOAD_WARN_BW: InfraPayload = {
  ...PAYLOAD_FULL,
  render: {
    ...(PAYLOAD_FULL.render as Extract<typeof PAYLOAD_FULL.render, { available: true }>),
    bandwidth_bytes_this_month: 4_294_967_296, // 4 GB — 80 %
  },
};

const PAYLOAD_BAD_BW: InfraPayload = {
  ...PAYLOAD_FULL,
  render: {
    ...(PAYLOAD_FULL.render as Extract<typeof PAYLOAD_FULL.render, { available: true }>),
    bandwidth_bytes_this_month: 4_831_838_208, // 4.5 GB — 90 %
  },
};

// ---------------------------------------------------------------------------
// fetch stub helper
// ---------------------------------------------------------------------------

function respond(body: InfraPayload) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

afterEach(() => vi.unstubAllGlobals());

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("InfraCostsPage", () => {
  it("renders headline cards with the monthly cost", async () => {
    respond(PAYLOAD_FULL);
    render(<InfraCostsPage />);
    await waitFor(() => {
      expect(screen.getByText("Costo mensual estimado")).toBeInTheDocument();
      expect(screen.getByText("32")).toBeInTheDocument();
    });
  });

  it("renders Supabase DB size and mail file count", async () => {
    respond(PAYLOAD_FULL);
    render(<InfraCostsPage />);
    await waitFor(() => {
      // Panel title rendered in h2.
      expect(screen.getByRole("heading", { name: "Supabase" })).toBeInTheDocument();
      // "Archivos de correo" appears both in the headline card and in the panel.
      expect(screen.getAllByText("Archivos de correo").length).toBeGreaterThan(0);
    });
  });

  it("shows the missing-key note when Render is unavailable", async () => {
    respond(PAYLOAD_NO_RENDER);
    render(<InfraCostsPage />);
    await waitFor(() => {
      expect(screen.getByText(/ORIGENLAB_RENDER_API_KEY/)).toBeInTheDocument();
    });
  });

  it("renders the Render section with service name when available", async () => {
    respond(PAYLOAD_FULL);
    render(<InfraCostsPage />);
    await waitFor(() => {
      expect(screen.getByText("origenlab-api")).toBeInTheDocument();
    });
  });

  it("shows a warning tone badge at 70-89% bandwidth", async () => {
    respond(PAYLOAD_WARN_BW);
    render(<InfraCostsPage />);
    await waitFor(() => {
      // The attention message appears near the bandwidth bar.
      expect(screen.getByText(/Atención.*70/)).toBeInTheDocument();
    });
  });

  it("shows a bad tone and overage warning at 90%+ bandwidth", async () => {
    respond(PAYLOAD_BAD_BW);
    render(<InfraCostsPage />);
    await waitFor(() => {
      expect(screen.getByText(/límite/)).toBeInTheDocument();
    });
  });

  it("shows the usage summary block", async () => {
    respond(PAYLOAD_FULL);
    render(<InfraCostsPage />);
    await waitFor(() => {
      expect(screen.getByText("¿Usamos lo que pagamos?")).toBeInTheDocument();
    });
  });

  it("has an Actualizar button", async () => {
    respond(PAYLOAD_FULL);
    render(<InfraCostsPage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /actualizar/i })).toBeInTheDocument();
    });
  });

  it("renders the plans_as_of date in the explanation text", async () => {
    respond(PAYLOAD_FULL);
    render(<InfraCostsPage />);
    await waitFor(() => {
      expect(screen.getByText(/2026-10-30/)).toBeInTheDocument();
    });
  });
});
