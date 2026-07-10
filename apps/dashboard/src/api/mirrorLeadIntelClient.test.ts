import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  downloadLeadProspectsExportCsv,
  mirrorLeadProspectsExportUrl,
  MIRROR_LEADS_PROSPECTS_EXPORT_PATH,
} from "./mirrorLeadIntelClient";
import { OperatorApiError } from "./operatorClient";

function stubObjectUrlApis() {
  const createObjectURL = vi.fn(() => "blob:prospectos-export");
  const revokeObjectURL = vi.fn();
  class TestURL extends URL {
    static createObjectURL = createObjectURL;
    static revokeObjectURL = revokeObjectURL;
  }
  vi.stubGlobal("URL", TestURL);
  return { createObjectURL, revokeObjectURL };
}

describe("mirrorLeadProspectsExportUrl", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_OPERATOR_API_BASE_URL", "https://api.example.test");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.innerHTML = "";
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

  it("downloads CSV with GET credentials, server filename, and object URL cleanup", async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrlApis();
    const blob = new Blob(["\ufefforganization,email\nOrigen,contacto@example.test\n"], {
      type: "text/csv;charset=utf-8",
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => blob,
      headers: new Headers({
        "Content-Disposition": 'attachment; filename="prospectos-ready.csv"',
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const clickedAnchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedAnchors.push(this);
    });

    await downloadLeadProspectsExportCsv({
      export_queue: "ready_to_contact",
      source_type: "gmail_historico",
    });

    expect(fetchMock.mock.calls[0][0]).toContain(MIRROR_LEADS_PROSPECTS_EXPORT_PATH);
    expect(fetchMock.mock.calls[0][0]).toContain("export_queue=ready_to_contact");
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickedAnchors).toHaveLength(1);
    expect(clickedAnchors[0].href).toBe("blob:prospectos-export");
    expect(clickedAnchors[0].download).toBe("prospectos-ready.csv");
    expect(document.body.contains(clickedAnchors[0])).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:prospectos-export");
  });

  it("uses a safe fallback filename when Content-Disposition is missing", async () => {
    stubObjectUrlApis();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: async () => new Blob(["organization,email\n"]),
        headers: new Headers(),
      }),
    );
    const clickedAnchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedAnchors.push(this);
    });

    await downloadLeadProspectsExportCsv({ export_queue: "all_visible" });

    expect(clickedAnchors[0].download).toBe("prospectos-export.csv");
  });

  it("throws OperatorApiError and skips DOM download work when export fails", async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrlApis();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        text: async () => '{"error":{"code":"backend_unavailable"}}',
      }),
    );

    await expect(
      downloadLeadProspectsExportCsv({ export_queue: "ready_to_contact" }),
    ).rejects.toBeInstanceOf(OperatorApiError);

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(revokeObjectURL).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
  });
});
