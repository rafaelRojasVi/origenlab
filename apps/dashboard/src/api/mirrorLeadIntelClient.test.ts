import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  mirrorLeadProspectsExportUrl,
  MIRROR_LEADS_PROSPECTS_EXPORT_PATH,
} from "./mirrorLeadIntelClient";

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
