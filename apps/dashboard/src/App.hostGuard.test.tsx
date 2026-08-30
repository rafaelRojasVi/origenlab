import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./pages/DashboardApp", () => ({
  DashboardApp: () => <div data-testid="dashboard-app">Dashboard</div>,
}));

const fetchTodayPanel = vi.fn();
const fetchWarmCases = vi.fn();

vi.mock("./api/operatorClient", () => ({
  fetchTodayPanel,
  fetchWarmCases,
  fetchContactProfile: vi.fn(),
  getOperatorApiBaseUrl: vi.fn(() => ""),
  OperatorApiError: class OperatorApiError extends Error {},
}));

import App from "./App";

function mockHostname(hostname: string) {
  vi.stubGlobal("location", {
    ...window.location,
    hostname,
    host: hostname,
    href: `https://${hostname}/`,
    origin: `https://${hostname}`,
  });
}

describe("App production host guard", () => {
  beforeEach(() => {
    vi.stubEnv("MODE", "production");
    fetchTodayPanel.mockReset();
    fetchWarmCases.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("renders DashboardApp on dashboard.origenlab.cl", () => {
    mockHostname("dashboard.origenlab.cl");
    render(<App />);
    expect(screen.getByTestId("dashboard-app")).toBeTruthy();
    expect(screen.queryByText("Private dashboard")).toBeNull();
  });

  it("renders DashboardApp on localhost", () => {
    mockHostname("localhost");
    render(<App />);
    expect(screen.getByTestId("dashboard-app")).toBeTruthy();
  });

  it("renders private placeholder on origenlab-dashboard.onrender.com", () => {
    mockHostname("origenlab-dashboard.onrender.com");
    render(<App />);
    expect(screen.getByText("Private dashboard")).toBeTruthy();
    expect(
      screen.getByText("Use the protected OrigenLab dashboard domain."),
    ).toBeTruthy();
    expect(screen.queryByTestId("dashboard-app")).toBeNull();
    expect(screen.queryByText(/warm/i)).toBeNull();
    expect(screen.queryByText(/equipment/i)).toBeNull();
    expect(screen.queryByText(/operator/i)).toBeNull();
  });

  it("does not call operator API client when host is disallowed", () => {
    mockHostname("origenlab-dashboard.onrender.com");
    render(<App />);
    expect(fetchTodayPanel).not.toHaveBeenCalled();
    expect(fetchWarmCases).not.toHaveBeenCalled();
  });
});
