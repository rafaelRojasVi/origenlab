import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { normalizeWarmCaseItem } from "../../api/commercialParse";
import type { WarmCaseItem } from "../../api/commercialTypes";
import { WarmCasesTable } from "./WarmCasesTable";

const row: WarmCaseItem = {
  case_id: "gmail-contacto-1",
  last_email_id: 1,
  last_seen_at: "2026-05-19T10:00:00-04:00",
  account_name: "ACME Lab",
  contact_email: "buyer@acme.cl",
  subject: "Re: centrifuge quote",
  category: "client_reply",
  status: "open",
  next_action: "follow_up",
  equipment_signal: "centrifuge",
  snippet: "Short preview only",
  gmail_url: null,
};

describe("WarmCasesTable", () => {
  it("shows Clientes reales preset on initial render without mailto links", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[row]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Clientes reales" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    screen.getByText(/vista: Clientes reales · solo lectura/);
    expect(screen.queryByRole("link", { name: "mailto" })).toBeNull();
    screen.getByRole("button", { name: "Copiar correo" });
    screen.getByRole("button", { name: "buyer@acme.cl" });
  });

  it("renders rows without body fields", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "sqlite",
          reduced_mode: false,
          note: "",
          count: 1,
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    screen.getByText("buyer@acme.cl");
    screen.getByText("ACME Lab");
    screen.getByText(/Short preview only/);
    expect(screen.queryByText(/body_preview/)).toBeNull();
  });

  it("shows loading state", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[]}
        meta={null}
        loading
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(document.querySelector('[role="status"]')).toBeTruthy();
  });

  it("shows error state", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[]}
        meta={null}
        loading={false}
        error="Warm cases failed"
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    screen.getByText("Warm cases failed");
    screen.getByRole("button", { name: "Reintentar" });
  });

  it("renders sparse row without crashing", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          normalizeWarmCaseItem(
            {
              last_seen_at: null,
              subject: null,
              category: undefined,
              status: null,
            },
            0,
          ),
        ]}
        meta={{
          data_source: "sqlite",
          reduced_mode: false,
          note: "",
          count: 1,
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Todo" }));
    const table = screen.getByRole("table");
    expect(table.textContent).toContain("Oportunidad");
  });

  it("shows empty state", () => {
    render(
      <WarmCasesTable
        backend="postgres"
        items={[]}
        meta={{
          data_source: "postgres_mirror",
          reduced_mode: true,
          note: "mirror lag",
          count: 0,
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    screen.getByText(/No hay casos tibios desde la API/);
    screen.getByText(/Espejo Postgres/);
  });

  it("calls onContactSelect when contact email is clicked", () => {
    const onContactSelect = vi.fn();
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "sqlite",
          reduced_mode: false,
          note: "",
          count: 1,
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={onContactSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "buyer@acme.cl" }));
    expect(onContactSelect).toHaveBeenCalledWith("buyer@acme.cl");
  });

  it("filters rows by search and shows no-match state", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            case_id: "other",
            contact_email: "other@elsewhere.cl",
            account_name: "Elsewhere",
          },
        ]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 2 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Buscar casos tibios"), {
      target: { value: "zzznomatch" },
    });
    screen.getByText(/Ningún caso coincide con la búsqueda o los filtros/);
    expect(screen.queryByText("other@elsewhere.cl")).toBeNull();
  });

  it("opens contact drilldown after filtering to a single row", () => {
    const onContactSelect = vi.fn();
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            case_id: "other",
            contact_email: "hidden@hidden.cl",
            account_name: "Hidden",
          },
        ]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 2 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={onContactSelect}
      />,
    );
    fireEvent.change(screen.getByLabelText("Buscar casos tibios"), {
      target: { value: "acme" },
    });
    fireEvent.click(screen.getByRole("button", { name: "buyer@acme.cl" }));
    expect(onContactSelect).toHaveBeenCalledWith("buyer@acme.cl");
  });

  it("shows filtered count in footer with active preset", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[row, { ...row, case_id: "b", contact_email: "x@y.cl" }]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 2 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    screen.getByText(/vista: Clientes reales/);
    screen.getByText(/Mostrando 1–2 de 2 casos/);
    fireEvent.change(screen.getByLabelText("Buscar casos tibios"), {
      target: { value: "buyer@acme" },
    });
    screen.getByText(/Mostrando 1 de 1 casos/);
    screen.getByText(/filtros activos/);
  });

  it("hides internal contacts by default; toggle shows them", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            case_id: "internal",
            contact_email: "contacto@origenlab.cl",
            account_name: "OrigenLab",
          },
        ]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 2 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
        initialFilters={{ preset: "todo" }}
      />,
    );
    expect(screen.queryByText("contacto@origenlab.cl")).toBeNull();
    screen.getByText("buyer@acme.cl");
    fireEvent.click(screen.getByLabelText("Ocultar contactos internos de OrigenLab"));
    screen.getByText("contacto@origenlab.cl");
    screen.getByText(/Mostrando 1–2 de 2 casos/);
  });

  it("section footer shows global warm queue total, not vague API reportó", () => {
    const paymentRows = [
      { ...row, case_id: "p1", category: "payment_admin" as const },
      { ...row, case_id: "p2", category: "payment_admin" as const, contact_email: "pay2@bank.cl" },
    ];
    render(
      <WarmCasesTable
        backend="sqlite"
        items={paymentRows}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 44 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
        sectionName="Pagos"
        globalQueueTotal={44}
        showViewPresets={false}
        initialFilters={{ preset: "todo", hideInternalContacts: false }}
      />,
    );
    screen.getByText(/Mostrando 1–2 de 2 en Pagos/);
    screen.getByText(/44 casos tibios en cola global/);
    expect(screen.queryByText(/API reportó/)).toBeNull();
  });

  it("paginates to 15 rows by default", () => {
    const many = Array.from({ length: 20 }, (_, i) => ({
      ...row,
      case_id: `case-${i}`,
      contact_email: `user${i}@acme.cl`,
    }));
    render(
      <WarmCasesTable
        backend="sqlite"
        items={many}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 20 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
        initialFilters={{ preset: "todo", hideInternalContacts: false }}
      />,
    );
    expect(document.querySelectorAll("tbody tr")).toHaveLength(15);
    const nav = within(screen.getByTestId("table-pagination-nav"));
    expect(nav.getByRole("button", { name: "Página 1" }).getAttribute("aria-current")).toBe("page");
    expect(nav.getByRole("button", { name: "Página 2" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filas por página"), { target: { value: "all" } });
    expect(document.querySelectorAll("tbody tr")).toHaveLength(20);
  });

  const auditRows: WarmCaseItem[] = [
    {
      ...row,
      case_id: "dhl",
      contact_email: "monica.silva@dhl.com",
      account_name: "DHL",
      category: "vendor_logistics",
      subject: "Import",
    },
    {
      ...row,
      case_id: "banco",
      contact_email: "serviciodetransferencias@bancochile.cl",
      account_name: "Banco Chile",
      category: "payment_admin",
      subject: "FACTURA",
    },
    {
      ...row,
      case_id: "dlab",
      contact_email: "chloe.yang@dlabsci.com",
      account_name: "DLAB",
      category: "supplier_reply",
    },
    {
      ...row,
      case_id: "ollital",
      contact_email: "kelly@ollital.com",
      account_name: "Ollital",
      category: "supplier_reply",
    },
    {
      ...row,
      case_id: "ortoalresa",
      contact_email: "carmen.llorente@ortoalresa.com",
      account_name: "Ortoalresa",
      category: "supplier_reply",
    },
    {
      ...row,
      case_id: "udec",
      contact_email: "tatiana.beldarrain@udec.cl",
      account_name: "UdeC",
      category: "client_reply",
      subject: "Consulta",
    },
    {
      ...row,
      case_id: "internal",
      contact_email: "contacto@origenlab.cl",
      account_name: "OrigenLab",
      category: "client_reply",
    },
  ];

  function renderAudit() {
    return render(
      <WarmCasesTable
        backend="sqlite"
        items={auditRows}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: auditRows.length }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
  }

  it("defaults to Clientes reales preset and shows UdeC only from audit set", () => {
    renderAudit();
    screen.getByText("tatiana.beldarrain@udec.cl");
    expect(screen.queryByText("monica.silva@dhl.com")).toBeNull();
    expect(screen.queryByText("serviciodetransferencias@bancochile.cl")).toBeNull();
    expect(screen.queryByText("chloe.yang@dlabsci.com")).toBeNull();
    screen.getByText(/vista: Clientes reales/);
  });

  it("Logística preset shows DHL not client rows", () => {
    renderAudit();
    fireEvent.click(screen.getByRole("button", { name: "Logística" }));
    screen.getByText("monica.silva@dhl.com");
    expect(screen.queryByText("tatiana.beldarrain@udec.cl")).toBeNull();
    screen.getByText(/vista: Logística/);
  });

  it("Pagos/admin preset shows Banco not client rows", () => {
    renderAudit();
    fireEvent.click(screen.getByRole("button", { name: "Pagos/admin" }));
    screen.getByText("serviciodetransferencias@bancochile.cl");
    expect(screen.queryByText("tatiana.beldarrain@udec.cl")).toBeNull();
    screen.getByText(/vista: Pagos\/admin/);
  });

  it("Pagos/admin preset shows payment_admin and transferencia snippet rows", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          {
            ...row,
            case_id: "pago-admin",
            contact_email: "tesoreria@hospital.cl",
            category: "payment_admin",
            subject: "Comprobante pago",
          },
          {
            ...row,
            case_id: "transferencia",
            contact_email: "notify@example.com",
            category: "opportunity",
            subject: "Aviso",
            snippet: "Confirmación de transferencia",
          },
          row,
        ]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 3 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Pagos/admin" }));
    screen.getByText("tesoreria@hospital.cl");
    screen.getByText("notify@example.com");
    expect(screen.queryByText("buyer@acme.cl")).toBeNull();
  });

  it("Clear filters resets search and preset to Clientes reales", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[row, { ...row, case_id: "b", contact_email: "x@y.cl" }]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 2 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Todo" }));
    expect(screen.getByRole("button", { name: "Todo" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.change(screen.getByLabelText("Buscar casos tibios"), {
      target: { value: "buyer@acme" },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Limpiar filtros y volver a Clientes reales",
      }),
    );
    expect(screen.getByRole("button", { name: "Clientes reales" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    screen.getByText(/vista: Clientes reales · solo lectura/);
    expect((screen.getByLabelText("Buscar casos tibios") as HTMLInputElement).value).toBe("");
  });

  it("Proveedores preset shows DLAB, Ollital, Ortoalresa", () => {
    renderAudit();
    fireEvent.click(screen.getByRole("button", { name: "Proveedores" }));
    screen.getByText("chloe.yang@dlabsci.com");
    screen.getByText("kelly@ollital.com");
    screen.getByText("carmen.llorente@ortoalresa.com");
    expect(screen.queryByText("monica.silva@dhl.com")).toBeNull();
    expect(screen.queryByText("tatiana.beldarrain@udec.cl")).toBeNull();
  });

  it("Todo preset shows all non-internal rows when hide-internal is on", () => {
    renderAudit();
    fireEvent.click(screen.getByRole("button", { name: "Todo" }));
    screen.getByText("monica.silva@dhl.com");
    screen.getByText("tatiana.beldarrain@udec.cl");
    expect(screen.queryByText("contacto@origenlab.cl")).toBeNull();
  });

  it("shows category fallback for unmapped next_action instead of Sin clasificar", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          {
            ...row,
            category: "client_opportunity",
            next_action: "unknown_token",
          },
        ]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
        showViewPresets={false}
        initialFilters={{ preset: "todo", hideInternalContacts: false }}
      />,
    );
    expect(screen.queryByText("Sin clasificar")).toBeNull();
    screen.getByText(/Validar equipo, especificaciones, precio y disponibilidad/i);
  });

  it("formats last_seen_at without raw ISO", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[row]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(screen.queryByText("2026-05-19T10:00:00-04:00")).toBeNull();
    expect(screen.getByText(/2026/)).toBeTruthy();
  });

  it("shows grouped email badge when count is above one", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          {
            ...row,
            subject: "IKA long thread",
            grouped_email_count: 13,
          },
        ]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(screen.getByTestId("warm-case-grouped-email-count").textContent).toMatch(
      /13 correos agrupados/,
    );
  });

  it("shows grouped CRTOP subject with inline email count", () => {
    render(
      <WarmCasesTable
        backend="sqlite"
        items={[
          {
            ...row,
            case_id: "crtop-1",
            account_name: "CRTOP",
            contact_email: "ariel@crtopmachine.com",
            subject: "Re: Thank you very much for your inquiry about our reactor.",
            equipment_signal: "reactor",
            category: "supplier_quote_received",
            grouped_email_count: 6,
          },
        ]}
        meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Todo" }));
    screen.getByText("CRTOP — Reactor OLT-HP-5L ×6");
    expect(screen.queryByText(/6 correos/)).toBeNull();
  });

  describe("local review labels", () => {
    const WARM_CASE_REVIEW_LABELS_STORAGE_KEY = "origenlab.dashboard.warmCaseReviewLabels.v1";

    beforeEach(() => {
      window.localStorage.clear();
    });

    it("renders review label control in each row", () => {
      render(
        <WarmCasesTable
          backend="sqlite"
          items={[row]}
          meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
          loading={false}
          error={null}
          onRetry={() => {}}
          onContactSelect={() => {}}
        />,
      );
      const select = screen.getByLabelText(
        "Etiqueta de revisión para buyer@acme.cl",
      ) as HTMLSelectElement;
      expect(select.value).toBe("");
      expect(select.textContent).toContain("Sin revisar");
      expect(select.textContent).toContain("Útil");
    });

    it("persists selected label in localStorage", () => {
      render(
        <WarmCasesTable
          backend="sqlite"
          items={[row]}
          meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
          loading={false}
          error={null}
          onRetry={() => {}}
          onContactSelect={() => {}}
        />,
      );
      fireEvent.change(screen.getByLabelText("Etiqueta de revisión para buyer@acme.cl"), {
        target: { value: "util" },
      });
      const stored = JSON.parse(
        window.localStorage.getItem(WARM_CASE_REVIEW_LABELS_STORAGE_KEY) ?? "{}",
      );
      expect(stored[row.case_id]).toBe("util");
    });

    it("does not open detail drawer when selecting a review label", () => {
      render(
        <WarmCasesTable
          backend="sqlite"
          items={[row]}
          meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 1 }}
          loading={false}
          error={null}
          onRetry={() => {}}
          onContactSelect={() => {}}
        />,
      );
      fireEvent.change(screen.getByLabelText("Etiqueta de revisión para buyer@acme.cl"), {
        target: { value: "util" },
      });
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("shows reviewed count after labeling a case", () => {
      render(
        <WarmCasesTable
          backend="sqlite"
          items={[row, { ...row, case_id: "other", contact_email: "other@elsewhere.cl" }]}
          meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 2 }}
          loading={false}
          error={null}
          onRetry={() => {}}
          onContactSelect={() => {}}
        />,
      );
      expect(screen.queryByTestId("warm-case-review-count")).toBeNull();
      fireEvent.change(screen.getByLabelText("Etiqueta de revisión para buyer@acme.cl"), {
        target: { value: "util" },
      });
      expect(screen.getByTestId("warm-case-review-count").textContent).toMatch(
        /1 revisado en este navegador/,
      );
    });

    it("filters rows by local review label", () => {
      window.localStorage.setItem(
        WARM_CASE_REVIEW_LABELS_STORAGE_KEY,
        JSON.stringify({ [row.case_id]: "util", other: "no_util" }),
      );
      render(
        <WarmCasesTable
          backend="sqlite"
          items={[
            row,
            { ...row, case_id: "other", contact_email: "other@elsewhere.cl", account_name: "Elsewhere" },
          ]}
          meta={{ data_source: "sqlite", reduced_mode: false, note: "", count: 2 }}
          loading={false}
          error={null}
          onRetry={() => {}}
          onContactSelect={() => {}}
          initialFilters={{ preset: "todo", hideInternalContacts: false }}
        />,
      );
      fireEvent.change(screen.getByLabelText("Filtrar por etiqueta de revisión local"), {
        target: { value: "util" },
      });
      screen.getByText("buyer@acme.cl");
      expect(screen.queryByText("other@elsewhere.cl")).toBeNull();
    });
  });
});
