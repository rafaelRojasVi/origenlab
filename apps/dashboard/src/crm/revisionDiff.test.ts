import { describe, expect, it } from "vitest";
import type { QuoteContents, QuoteLine, RevisionCard } from "./crmTypes";
import { revisionChanges } from "./revisionDiff";

// Every value below is invented; the repository is public.
function line(over: Partial<QuoteLine>): QuoteLine {
  return {
    line_no: 1,
    item: null,
    parent_item: null,
    kind: null,
    brand: null,
    model: null,
    description: null,
    qty: "1",
    unit_price: "100000",
    line_total: "100000",
    optional: false,
    check_status: "ok",
    ...over,
  };
}

function contents(net: string, lines: QuoteLine[]): QuoteContents {
  return { currency: "CLP", net_total: net, net_source: "document", tax_total: null, gross_total: null, lines };
}

function rev(no: number, c: QuoteContents | null): RevisionCard {
  return {
    revision_id: `r${no}`,
    revision_no: no,
    status: "sent",
    origin: "cotizador",
    sent_at: "2026-10-29T12:00:00Z",
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: null,
    drive: null,
    quote_number: "01318-26",
    contents: c,
  };
}

const equipo = line({ model: "UP200St", unit_price: "10980000", line_total: "10980000" });
const sonotrodo = line({ line_no: 2, model: "S26d14", unit_price: "1420000", line_total: "1420000" });
const soporte = line({ line_no: 3, description: "Soporte ST1-16 con brida", unit_price: "600000", line_total: "600000" });

describe("revisionChanges", () => {
  it("names the neto change", () => {
    const out = revisionChanges(rev(1, contents("12400000", [equipo, sonotrodo])), rev(2, contents("12000000", [equipo, sonotrodo])));
    expect(out).toEqual(["Neto $ 12.400.000 → $ 12.000.000"]);
  });

  it("lists an added and a removed line, after the neto", () => {
    const out = revisionChanges(
      rev(1, contents("12400000", [equipo, sonotrodo])),
      rev(2, contents("11580000", [equipo, soporte])),
    );
    expect(out).toEqual(["Neto $ 12.400.000 → $ 11.580.000", "+ Soporte ST1-16 con brida", "− S26d14"]);
  });

  it("matches lines by model, case-insensitive, and names a quantity and a price change", () => {
    const out = revisionChanges(
      rev(1, contents("12400000", [equipo, sonotrodo])),
      rev(2, contents("12400000", [{ ...equipo, model: "up200st", qty: "2" }, { ...sonotrodo, unit_price: "1300000" }])),
    );
    expect(out).toEqual(["up200st: cantidad 1 → 2", "S26d14: precio $ 1.420.000 → $ 1.300.000"]);
  });

  it("cannot compare when either PDF was not read", () => {
    expect(revisionChanges(rev(1, null), rev(2, contents("1", [equipo])))).toEqual(["PDF sin leer: no se puede comparar"]);
    expect(revisionChanges(rev(1, contents("1", [equipo])), rev(2, null))).toEqual(["PDF sin leer: no se puede comparar"]);
  });

  it("says so when nothing changed", () => {
    const c = contents("12400000", [equipo, sonotrodo]);
    expect(revisionChanges(rev(1, c), rev(2, { ...c, lines: [{ ...sonotrodo }, { ...equipo }] }))).toEqual(["Sin cambios en líneas ni neto"]);
  });
});
