import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  downloadLeadProspectsExportCsv,
  mirrorLeadProspectsExportUrl,
  MIRROR_LEADS_PROSPECTS_EXPORT_PATH,
} from "./mirrorLeadIntelClient";
import { OperatorApiError } from "./operatorClient";

const originalCreateObjectURL = URL.createObjectURL;
const originalRevokeObjectURL = URL.revokeObjectURL;

describe("mirrorLeadProspectsExportUrl", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "https://api.example.test");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: originalCreateObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: originalRevokeObjectURL,
    });
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
});

describe("downloadLeadProspectsExportCsv", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "https://api.example.test");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: originalCreateObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: originalRevokeObjectURL,
    });
    document.body.innerHTML = "";
  });

  function stubObjectUrlApis() {
    const createObjectURL = vi.fn(() => "blob:prospects-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    return { createObjectURL, revokeObjectURL };
  }

  it("downloads CSV with credentials, response filename, click, and URL cleanup", async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrlApis();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("rut,nombre\n1,Acme\n", {
        status: 200,
        headers: {
          "Content-Disposition": 'attachment; filename="prospectos-ready.csv"',
          "Content-Type": "text/csv; charset=utf-8",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
      expect(this.href).toBe("blob:prospects-export");
      expect(this.download).toBe("prospectos-ready.csv");
      expect(this.isConnected).toBe(true);
    });

    await downloadLeadProspectsExportCsv({
      export_queue: "ready_to_contact",
      q: "Acme",
      limit: 100,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/mirror/leads/prospects/export.csv"),
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
    expect(fetchMock.mock.calls[0][0]).toContain("export_queue=ready_to_contact");
    expect(fetchMock.mock.calls[0][0]).toContain("q=Acme");
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(document.querySelector('a[download="prospectos-ready.csv"]')).toBeNull();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:prospects-export");
  });

  it("falls back to the default export filename when the API omits one", async () => {
    stubObjectUrlApis();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("rut,nombre\n1,Acme\n", { status: 200 })),
    );
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
      expect(this.download).toBe("prospectos-export.csv");
    });

    await downloadLeadProspectsExportCsv({ export_queue: "follow_up" });

    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it("throws OperatorApiError on non-2xx responses without creating a download", async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrlApis();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("backend unavailable", { status: 503 })),
    );
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await expect(
      downloadLeadProspectsExportCsv({ export_queue: "ready_to_contact" }),
    ).rejects.toMatchObject({
      name: "OperatorApiError",
      status: 503,
      message: "backend unavailable",
    } satisfies Partial<OperatorApiError>);

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });
});
