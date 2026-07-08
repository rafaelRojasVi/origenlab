import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  downloadLeadProspectsExportCsv,
  mirrorLeadProspectsExportUrl,
  MIRROR_LEADS_PROSPECTS_EXPORT_PATH,
} from "./mirrorLeadIntelClient";
import { OperatorApiError } from "./operatorClient";

describe("mirrorLeadProspectsExportUrl", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_OPERATOR_API_BASE_URL", "https://api.example.test");
  });

  it("includes export queue and current list filters", () => {
    const url = mirrorLeadProspectsExportUrl({
      export_queue: "ready_to_contact",
      q: "Acme",
      source_type: "gmail_historico",
      sector: "Salud",
      min_score: 50,
      include_blocked: true,
      limit: 100,
    });
    expect(url).toContain(MIRROR_LEADS_PROSPECTS_EXPORT_PATH);
    expect(url).toContain("export_queue=ready_to_contact");
    expect(url).toContain("q=Acme");
    expect(url).toContain("source_type=gmail_historico");
    expect(url).toContain("sector=Salud");
    expect(url).toContain("min_score=50");
    expect(url).toContain("include_blocked=true");
  });
});

describe("downloadLeadProspectsExportCsv", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_OPERATOR_API_BASE_URL", "https://api.example.test");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("downloads the CSV using the server-provided attachment filename", async () => {
    const blob = new Blob(["\ufefforganization_name\nAcme Labs\n"], { type: "text/csv" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => blob,
      headers: new Headers({
        "Content-Disposition": 'attachment; filename="prospectos-ready-to-contact.csv"',
      }),
    });
    const createObjectURL = vi.fn(() => "blob:prospectos-export");
    const revokeObjectURL = vi.fn();
    const clickedDownloads: string[] = [];
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedDownloads.push(this.download);
    });

    await downloadLeadProspectsExportCsv({
      export_queue: "ready_to_contact",
      q: " Acme ",
      limit: 100,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("export_queue=ready_to_contact"),
      { method: "GET", credentials: "include" },
    );
    expect(fetchMock.mock.calls[0][0]).toContain("q=Acme");
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickedDownloads).toEqual(["prospectos-ready-to-contact.csv"]);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:prospectos-export");
    expect(document.querySelector('a[download="prospectos-ready-to-contact.csv"]')).toBeNull();
  });

  it("raises an OperatorApiError without creating a download on export failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: "Unprocessable Entity",
      text: async () => "invalid export_queue",
    });
    const createObjectURL = vi.fn(() => "blob:should-not-happen");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    await expect(
      downloadLeadProspectsExportCsv({ export_queue: "ready_to_contact" }),
    ).rejects.toBeInstanceOf(OperatorApiError);
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });
});
