import { describe, expect, it } from "vitest";
import type { WarmCaseItem } from "../api/commercialTypes";
import {
  DEFAULT_WARM_VIEW_PRESET,
  filterWarmCasesByViewPreset,
  isExcludedFromClientesReales,
  matchesWarmCaseViewPreset,
} from "./warmCaseViewPreset";

function warmRow(
  partial: Partial<WarmCaseItem> & Pick<WarmCaseItem, "contact_email" | "category">,
): WarmCaseItem {
  return {
    case_id: partial.case_id ?? partial.contact_email,
    last_email_id: 1,
    last_seen_at: "2026-05-19T10:00:00Z",
    account_name: partial.account_name ?? "",
    subject: partial.subject ?? "",
    status: partial.status ?? "open",
    next_action: partial.next_action ?? "",
    equipment_signal: partial.equipment_signal ?? "",
    snippet: partial.snippet ?? "",
    gmail_url: null,
    ...partial,
  };
}

const AUDIT_ROWS: WarmCaseItem[] = [
  warmRow({
    case_id: "dhl",
    contact_email: "monica.silva@dhl.com",
    category: "vendor_logistics",
    account_name: "DHL",
  }),
  warmRow({
    case_id: "banco",
    contact_email: "serviciodetransferencias@bancochile.cl",
    category: "payment_admin",
    account_name: "Banco Chile",
  }),
  warmRow({
    case_id: "dlab",
    contact_email: "chloe.yang@dlabsci.com",
    category: "supplier_reply",
    account_name: "DLAB",
  }),
  warmRow({
    case_id: "ollital",
    contact_email: "kelly@ollital.com",
    category: "supplier_reply",
    account_name: "Ollital",
  }),
  warmRow({
    case_id: "ortoalresa",
    contact_email: "carmen.llorente@ortoalresa.com",
    category: "supplier_reply",
    account_name: "Ortoalresa",
  }),
  warmRow({
    case_id: "udec",
    contact_email: "tatiana.beldarrain@udec.cl",
    category: "client_reply",
    account_name: "UdeC",
  }),
  warmRow({
    case_id: "internal",
    contact_email: "contacto@origenlab.cl",
    category: "client_reply",
    account_name: "OrigenLab",
  }),
  warmRow({
    case_id: "equip",
    contact_email: "lab@hospital.cl",
    category: "client_reply",
    equipment_signal: "centrifuge",
    account_name: "Hospital",
  }),
];

describe("warmCaseViewPreset", () => {
  it("defaults to Clientes reales", () => {
    expect(DEFAULT_WARM_VIEW_PRESET).toBe("clientes_reales");
  });

  it("Clientes reales includes UdeC client_reply and excludes logistics, payments, suppliers", () => {
    const preset = "clientes_reales" as const;
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[5], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[0], preset)).toBe(false);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[1], preset)).toBe(false);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[2], preset)).toBe(false);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[3], preset)).toBe(false);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[4], preset)).toBe(false);
  });

  it("Proveedores includes DLAB, Ollital, Ortoalresa and excludes DHL logistics", () => {
    const preset = "proveedores" as const;
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[2], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[3], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[4], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[0], preset)).toBe(false);
  });

  it("Pagos/admin includes Banco Chile payment_admin", () => {
    const preset = "pagos_admin" as const;
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[1], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[5], preset)).toBe(false);
  });

  it("Pagos/admin includes bancochile.cl and FACTURA/transferencia text signals", () => {
    const preset = "pagos_admin" as const;
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "alertas@bancochile.cl",
          category: "opportunity",
          subject: "Movimiento",
        }),
        preset,
      ),
    ).toBe(true);
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "notify@example.com",
          category: "opportunity",
          subject: "FACTURA 6",
        }),
        preset,
      ),
    ).toBe(true);
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "notify@example.com",
          category: "opportunity",
          subject: "Hola",
          snippet: "Confirmación de transferencia bancaria",
        }),
        preset,
      ),
    ).toBe(true);
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "notify@example.com",
          category: "client_reply",
          subject: "Consulta equipo",
        }),
        preset,
      ),
    ).toBe(false);
  });

  it("Logística includes DHL vendor_logistics", () => {
    const preset = "logistica" as const;
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[0], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[1], preset)).toBe(false);
  });

  it("Con señal de equipo matches non-empty equipment_signal", () => {
    const preset = "con_senal_equipo" as const;
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[7], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(AUDIT_ROWS[5], preset)).toBe(false);
  });

  it("Todo includes all loaded rows", () => {
    expect(filterWarmCasesByViewPreset(AUDIT_ROWS, "todo")).toHaveLength(AUDIT_ROWS.length);
  });

  const FALSE_POSITIVE_CLIENT_REPLY: WarmCaseItem[] = [
    warmRow({
      case_id: "google-security",
      contact_email: "no-reply@accounts.google.com",
      category: "client_reply",
      subject: "Alerta de seguridad",
    }),
    warmRow({
      case_id: "eppendorf",
      contact_email: "eppendorf@eppendorf.com",
      category: "client_reply",
      subject: "Please confirm your registration!",
    }),
    warmRow({
      case_id: "valuen",
      contact_email: "sales@valuenindustrial.com",
      category: "client_reply",
      subject: "Industrial valves",
    }),
    warmRow({
      case_id: "fanbolun",
      contact_email: "sales001@gzfanbolun.com",
      category: "client_reply",
      subject: "Equipment promo",
    }),
    warmRow({
      case_id: "yuanhuai",
      contact_email: "jizhendong@yuanhuai.com",
      category: "client_reply",
      subject: "YHCHEM offer",
    }),
  ];

  it("Clientes reales excludes internal contacto@origenlab.cl waiting_client", () => {
    const preset = "clientes_reales" as const;
    const row = warmRow({
      contact_email: "contacto@origenlab.cl",
      category: "waiting_client",
      subject: "Re: Quotation Request / New adress created",
    });
    expect(matchesWarmCaseViewPreset(row, preset)).toBe(false);
  });

  it("Pagos/admin includes bancochile FACTURA and CEAF bank-details thread", () => {
    const preset = "pagos_admin" as const;
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "serviciodetransferencias@bancochile.cl",
          category: "payment_admin",
          subject: "FACTURA 6",
        }),
        preset,
      ),
    ).toBe(true);
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "lhidalgo@ceaf.cl",
          category: "client_reply",
          subject: "Solicita datos Bancarios",
        }),
        preset,
      ),
    ).toBe(true);
  });

  it("Logística includes DHL vendor_logistics and import-account subject", () => {
    const preset = "logistica" as const;
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "monica.silva@dhl.com",
          category: "vendor_logistics",
          subject: "PROPUESTA COMERCIAL DHL",
        }),
        preset,
      ),
    ).toBe(true);
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "monica.silva@dhl.com",
          category: "client_reply",
          subject: "Solicitud cuenta importación",
        }),
        preset,
      ),
    ).toBe(true);
  });

  it("Clientes reales excludes CEAF bank-details thread (Pagos/admin only)", () => {
    const preset = "clientes_reales" as const;
    const row = warmRow({
      contact_email: "lhidalgo@ceaf.cl",
      category: "client_reply",
      subject: "Solicita datos Bancarios",
      snippet: "factura N°06 y proceder al pago",
    });
    expect(isExcludedFromClientesReales(row)).toBe(true);
    expect(matchesWarmCaseViewPreset(row, preset)).toBe(false);
    expect(matchesWarmCaseViewPreset(row, "pagos_admin")).toBe(true);
    expect(matchesWarmCaseViewPreset(row, "todo")).toBe(true);
  });

  it("Clientes reales includes CEAF OC threads", () => {
    const preset = "clientes_reales" as const;
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "cgaray@ceaf.cl",
          category: "client_reply",
          subject: "Remite OC N º 26172",
        }),
        preset,
      ),
    ).toBe(true);
    expect(
      matchesWarmCaseViewPreset(
        warmRow({
          contact_email: "fgonzalez@ceaf.cl",
          category: "waiting_supplier",
          subject: "Re: Remite OC N º 26172",
        }),
        preset,
      ),
    ).toBe(true);
  });

  it("Clientes reales excludes audit false positives even when mislabeled client_reply", () => {
    const preset = "clientes_reales" as const;
    for (const row of FALSE_POSITIVE_CLIENT_REPLY) {
      expect(isExcludedFromClientesReales(row)).toBe(true);
      expect(matchesWarmCaseViewPreset(row, preset)).toBe(false);
    }
  });

  it("Proveedores includes mislabeled supplier vendor domains under Todo path", () => {
    const preset = "proveedores" as const;
    expect(matchesWarmCaseViewPreset(FALSE_POSITIVE_CLIENT_REPLY[2], preset)).toBe(true);
    expect(matchesWarmCaseViewPreset(FALSE_POSITIVE_CLIENT_REPLY[4], preset)).toBe(true);
    expect(filterWarmCasesByViewPreset(FALSE_POSITIVE_CLIENT_REPLY, "todo")).toHaveLength(5);
  });

  it("excludes Cyber campaign rows from Clientes reales and shows them in Campañas / marketing", () => {
    const cyber = warmRow({
      contact_email: "lab@example.cl",
      category: "campaign_outreach",
      subject: "CYBERDAY — equipos de laboratorio seleccionados hasta el 7 de junio",
      status: "waiting",
    });
    expect(isExcludedFromClientesReales(cyber)).toBe(true);
    expect(matchesWarmCaseViewPreset(cyber, "clientes_reales")).toBe(false);
    expect(matchesWarmCaseViewPreset(cyber, "campanas_marketing")).toBe(true);
  });

  it("excludes auto_acknowledgement from Clientes reales", () => {
    const idiem = warmRow({
      contact_email: "contacto@idiem.cl",
      category: "auto_acknowledgement",
      subject: "Acuse de recibo",
      snippet: "Hemos recibido su mensaje",
      status: "problem",
    });
    expect(matchesWarmCaseViewPreset(idiem, "clientes_reales")).toBe(false);
    expect(matchesWarmCaseViewPreset(idiem, "campanas_marketing")).toBe(true);
  });
});
