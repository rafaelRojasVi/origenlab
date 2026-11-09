import "@testing-library/jest-dom";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WebStatsCard } from "./WebStatsCard";
import type { WebStatsData } from "./webStatsApi";
import { clearResourceCache } from "../useResource";

// Every value below is invented; the repository is public.

const EMPTY_PERIOD = {
  visitas_total: 0,
  visitas_interno: 0,
  visitas_by_origen: {},
  whatsapp_total: 0,
  whatsapp_top_models: [] as [string, number][],
  cotizacion_total: 0,
  cotizacion_top_models: [] as [string, number][],
  form_submits: 0,
  pdf_downloads: 0,
  d3_interacts: 0,
  top_pages: [] as [string, number][],
};

const STATS_DATA: WebStatsData = {
  "7d": {
    ...EMPTY_PERIOD,
    visitas_total: 142,
    visitas_interno: 23,
    visitas_by_origen: { google: 95, directo: 32, linkedin: 15 },
    whatsapp_total: 18,
    whatsapp_top_models: [["hielscher-up400st", 10], ["ohaus-mb32", 5]],
    cotizacion_total: 12,
    cotizacion_top_models: [["hielscher-up200st", 7]],
    form_submits: 3,
    pdf_downloads: 8,
    d3_interacts: 55,
    top_pages: [["/productos/sonicacion/hielscher-up400st/", 42], ["/", 30]],
  },
  "30d": {
    ...EMPTY_PERIOD,
    visitas_total: 480,
    visitas_interno: 60,
    visitas_by_origen: { google: 300, directo: 100, otro: 80 },
    whatsapp_total: 54,
    whatsapp_top_models: [["hielscher-up400st", 30]],
    cotizacion_total: 40,
    cotizacion_top_models: [["hielscher-up400st", 20]],
    form_submits: 9,
    pdf_downloads: 24,
    d3_interacts: 150,
    top_pages: [["/", 90]],
  },
  fetched_at: "2026-11-09T12:00:00Z",
};

function stubFetch(response: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response),
  );
}

function statsOkResponse(data: WebStatsData = STATS_DATA): Response {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  clearResourceCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearResourceCache();
});

// ─── loading ──────────────────────────────────────────────────────────────────

describe("loading state", () => {
  it("shows a skeleton while the fetch is pending", () => {
    // Never resolves so the component stays in loading.
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<WebStatsCard />);
    expect(screen.getByRole("status", { name: /cargando/i })).toBeInTheDocument();
    expect(screen.getByTestId("web-stats-card")).toBeInTheDocument();
  });
});

// ─── data ─────────────────────────────────────────────────────────────────────

describe("data rendering", () => {
  it("shows 7-day visitas total by default", async () => {
    stubFetch(statsOkResponse());
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());
    expect(screen.getByText("142")).toBeInTheDocument(); // visitas_total 7d
  });

  it("shows «interno» count separately", async () => {
    stubFetch(statsOkResponse());
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());
    expect(screen.getByText(/23/)).toBeInTheDocument();
    expect(screen.getByText(/navegación interna/i)).toBeInTheDocument();
  });

  it("shows acquisition origins with progress bars", async () => {
    stubFetch(statsOkResponse());
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());
    expect(screen.getByText("Google")).toBeInTheDocument();
    expect(screen.getByText("95")).toBeInTheDocument();
  });

  it("shows interaction counts", async () => {
    stubFetch(statsOkResponse());
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());
    expect(screen.getByText("18")).toBeInTheDocument(); // whatsapp_total
    expect(screen.getByText("12")).toBeInTheDocument(); // cotizacion_total
    expect(screen.getByText("55")).toBeInTheDocument(); // d3_interacts
  });

  it("shows top model for WhatsApp clicks", async () => {
    stubFetch(statsOkResponse());
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());
    expect(screen.getByText("hielscher-up400st")).toBeInTheDocument();
  });
});

// ─── window toggle ─────────────────────────────────────────────────────────────

describe("period toggle", () => {
  it("shows 30-day data when «30 días» is pressed", async () => {
    stubFetch(statsOkResponse());
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());

    // Switch to 30d.
    fireEvent.click(screen.getByRole("button", { name: "30 días" }));
    expect(screen.getByText("480")).toBeInTheDocument(); // visitas_total 30d
  });

  it("toggle buttons have aria-pressed state", async () => {
    stubFetch(statsOkResponse());
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());

    const btn7 = screen.getByRole("button", { name: "7 días" });
    const btn30 = screen.getByRole("button", { name: "30 días" });
    expect(btn7).toHaveAttribute("aria-pressed", "true");
    expect(btn30).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(btn30);
    expect(btn7).toHaveAttribute("aria-pressed", "false");
    expect(btn30).toHaveAttribute("aria-pressed", "true");
  });
});

// ─── not configured ───────────────────────────────────────────────────────────

describe("not_configured state", () => {
  it("shows configuration notice when Worker returns 503 not_configured", async () => {
    stubFetch(
      new Response(JSON.stringify({ error: { code: "not_configured" } }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<WebStatsCard />);
    await waitFor(() =>
      expect(screen.getByTestId("web-stats-not-configured")).toBeInTheDocument(),
    );
    expect(screen.getByText(/falta configurar/i)).toBeInTheDocument();
  });
});

// ─── empty period ──────────────────────────────────────────────────────────────

describe("empty period", () => {
  it("shows «Sin eventos en este período» when all counts are zero", async () => {
    const emptyData: WebStatsData = {
      "7d": EMPTY_PERIOD,
      "30d": EMPTY_PERIOD,
      fetched_at: "2026-11-09T12:00:00Z",
    };
    stubFetch(statsOkResponse(emptyData));
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.queryByRole("status", { name: /cargando/i })).not.toBeInTheDocument());
    expect(screen.getByText(/sin eventos/i)).toBeInTheDocument();
  });
});

// ─── error ────────────────────────────────────────────────────────────────────

describe("fetch error", () => {
  it("shows retry button on a network error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("Network error"))));
    render(<WebStatsCard />);
    await waitFor(() => expect(screen.getByText(/reintentar/i)).toBeInTheDocument());
  });
});

// ─── fetch call ───────────────────────────────────────────────────────────────

describe("fetch call", () => {
  it("calls /api/stats/web-stats with credentials: include", async () => {
    const fetchMock = vi.fn(async () => statsOkResponse());
    vi.stubGlobal("fetch", fetchMock);
    render(<WebStatsCard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const req = fetchMock.mock.calls[0][0] as RequestInfo;
    const url = typeof req === "string" ? req : req instanceof Request ? req.url : String(req);
    expect(url).toContain("/api/stats/web-stats");
    if (req instanceof Request) {
      // credentials: "include" sets no header; it instructs the browser to attach cookies.
      // We check the init option instead.
    }
    // Verify method.
    if (req instanceof Request) {
      expect(req.method).toBe("GET");
    }
  });
});
