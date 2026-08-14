import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EquipmentOpportunityItem } from "../../api/commercialTypes";
import { EQUIPMENT_WATCHLIST_STORAGE_KEY } from "../../lib/equipmentWatchlist";
import { EquipmentOpportunitiesTable } from "./EquipmentOpportunitiesTable";

function createMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear() {
      data.clear();
    },
    getItem(key: string) {
      return data.get(key) ?? null;
    },
    key(index: number) {
      return [...data.keys()][index] ?? null;
    },
    removeItem(key: string) {
      data.delete(key);
    },
    setItem(key: string, value: string) {
      data.set(key, value);
    },
  };
}

const row: EquipmentOpportunityItem = {
  priority_rank: 1,
  codigo_licitacion: "LP-001",
  buyer: "Universidad Ejemplo",
  region: "RM",
  close_date: "01/06/2026",
  equipment_category: "centrifuge",
  item_description: "High-speed centrifuge",
  next_action: "quote_now",
  safe_channel: "mercado_publico_bid",
  supplier_needed: "yes",
  contact_status: "no_verified_buyer_email",
  contact_email: "buyer@hospital.cl",
  operator_note: "fit=90",
};

describe("EquipmentOpportunitiesTable", () => {
  let memoryStorage: Storage;

  beforeEach(() => {
    memoryStorage = createMemoryStorage();
    vi.stubGlobal("localStorage", memoryStorage);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders safe columns only", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    screen.getByText("Universidad Ejemplo");
    screen.getByText("LP-001");
    expect(screen.getAllByTestId("equipment-triage-badge").some((el) => el.textContent === "Cotizar ahora")).toBe(
      true,
    );
    expect(screen.queryByText(/source_path/)).toBeNull();
    expect(screen.queryByText(/body_preview/)).toBeNull();
  });

  it("renders minimal row without crashing", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="postgres"
        items={[
          {
            priority_rank: 0,
            codigo_licitacion: "",
            buyer: "",
            region: "",
            close_date: "",
            equipment_category: "",
            item_description: "",
            next_action: "",
            safe_channel: "",
            supplier_needed: "",
            contact_status: "",
            contact_email: "",
            operator_note: "",
          },
        ]}
        meta={{
          data_source: "postgres_mirror",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: null,
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows error and empty states", () => {
    const { rerender } = render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[]}
        meta={null}
        loading={false}
        error="Equipment API down"
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    screen.getByText("Equipment API down");

    rerender(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[]}
        meta={{ data_source: "active_current_csv", reduced_mode: false, note: "", count: 0, campaign_mode: null }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    screen.getByText(/No hay oportunidades de equipos en la cola actual/);
  });

  it("renders Cierre pronto triage badge for close dates within three days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-15T12:00:00Z"));
    try {
      render(
        <EquipmentOpportunitiesTable
          backend="postgres"
          items={[
            {
              ...row,
              next_action: "monitor",
              close_at: "2026-06-17T19:00:00-04:00",
              close_date: "",
            },
          ]}
          meta={{
            data_source: "postgres_mirror",
            reduced_mode: false,
            note: "",
            count: 1,
            campaign_mode: "equipment_first",
          }}
          loading={false}
          error={null}
          onRetry={() => {}}
          onContactSelect={() => {}}
        />,
      );
      expect(
        screen.getAllByTestId("equipment-triage-badge").some((el) => el.textContent === "Cierre pronto"),
      ).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders at most three triage badges per row", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="postgres"
        items={[
          {
            ...row,
            next_action: "quote_now",
            close_at: "2026-06-16T12:00:00Z",
            contact_email: "",
            contact_status: "no_verified_buyer_email",
            supplier_needed: "yes",
            safe_channel: "mercado_publico_only",
          },
        ]}
        meta={{
          data_source: "postgres_mirror",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(screen.getAllByTestId("equipment-triage-badge")).toHaveLength(3);
  });

  it("renders save button for each row", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: /Guardar oportunidad: Universidad Ejemplo/ })).toBeTruthy();
  });

  it("clicking save does not open the detail drawer", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Guardar oportunidad: Universidad Ejemplo/ }));
    expect(screen.queryByTestId("equipment-opportunity-detail-drawer")).toBeNull();
    screen.getByRole("button", { name: /Quitar de guardadas: Universidad Ejemplo/ });
  });

  it("filters saved items with Solo guardadas", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            codigo_licitacion: "LP-002",
            buyer: "Hospital Monitor",
            next_action: "monitor",
          },
        ]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 2,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Guardar oportunidad: Universidad Ejemplo/ }));
    fireEvent.change(screen.getByLabelText("Filtrar oportunidades de equipos por lista de seguimiento"), {
      target: { value: "saved" },
    });

    screen.getByText("Universidad Ejemplo");
    expect(screen.queryByText("Hospital Monitor")).toBeNull();
  });

  it("clear filters resets watchlist filter but keeps saved state", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            codigo_licitacion: "LP-002",
            buyer: "Hospital Monitor",
            next_action: "monitor",
          },
        ]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 2,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Guardar oportunidad: Universidad Ejemplo/ }));
    fireEvent.change(screen.getByLabelText("Filtrar oportunidades de equipos por lista de seguimiento"), {
      target: { value: "saved" },
    });
    fireEvent.change(screen.getByLabelText("Buscar oportunidades de equipos"), {
      target: { value: "zzznomatch" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));

    screen.getByRole("button", { name: /Quitar de guardadas: Universidad Ejemplo/ });
    screen.getByText("Hospital Monitor");
    expect(
      (screen.getByLabelText("Filtrar oportunidades de equipos por lista de seguimiento") as HTMLSelectElement).value,
    ).toBe("all");
  });

  it("persists saved state through rerender via localStorage", () => {
    const { unmount } = render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Guardar oportunidad: Universidad Ejemplo/ }));
    expect(memoryStorage.getItem(EQUIPMENT_WATCHLIST_STORAGE_KEY)).toContain("eq-1-LP-001");

    unmount();
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    screen.getByRole("button", { name: /Quitar de guardadas: Universidad Ejemplo/ });
  });

  it("opens contact drilldown when contact email is present", () => {
    const onContactSelect = vi.fn();
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={onContactSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "buyer@hospital.cl" }));
    expect(onContactSelect).toHaveBeenCalledWith("buyer@hospital.cl");
  });

  it("renders triage filter select", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(screen.getByLabelText("Filtrar oportunidades de equipos por triaje")).toBeTruthy();
  });

  it("filters rows when Cotizar ahora triage is selected", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            codigo_licitacion: "LP-002",
            buyer: "Hospital Monitor",
            next_action: "monitor",
          },
        ]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 2,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("Filtrar oportunidades de equipos por triaje"), {
      target: { value: "quote_now" },
    });

    screen.getByText("Universidad Ejemplo");
    expect(screen.queryByText("Hospital Monitor")).toBeNull();
  });

  it("filters rows when Sin contacto triage is selected", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[
          {
            ...row,
            codigo_licitacion: "LP-001",
            buyer: "Comprador Sin Contacto",
            contact_email: "",
            contact_status: "no_verified_buyer_email",
          },
          {
            ...row,
            codigo_licitacion: "LP-002",
            buyer: "Hospital Con Email",
            contact_email: "contacto@hospital.cl",
            contact_status: "review_required",
          },
        ]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 2,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("Filtrar oportunidades de equipos por triaje"), {
      target: { value: "missing_contact" },
    });

    screen.getByText("Comprador Sin Contacto");
    expect(screen.queryByText("Hospital Con Email")).toBeNull();
  });

  it("combines search and triage filters", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            codigo_licitacion: "LP-002",
            buyer: "Otra Universidad",
            next_action: "quote_now",
          },
        ]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 2,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("Buscar oportunidades de equipos"), {
      target: { value: "ejemplo" },
    });
    fireEvent.change(screen.getByLabelText("Filtrar oportunidades de equipos por triaje"), {
      target: { value: "quote_now" },
    });

    screen.getByText("Universidad Ejemplo");
    expect(screen.queryByText("Otra Universidad")).toBeNull();
  });

  it("clear filters resets search and triage", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[
          row,
          {
            ...row,
            codigo_licitacion: "LP-002",
            buyer: "Hospital Monitor",
            next_action: "monitor",
          },
        ]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 2,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("Buscar oportunidades de equipos"), {
      target: { value: "ejemplo" },
    });
    fireEvent.change(screen.getByLabelText("Filtrar oportunidades de equipos por triaje"), {
      target: { value: "quote_now" },
    });
    expect(screen.queryByText("Hospital Monitor")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));

    screen.getByText("Universidad Ejemplo");
    screen.getByText("Hospital Monitor");
    expect(
      (screen.getByLabelText("Filtrar oportunidades de equipos por triaje") as HTMLSelectElement).value,
    ).toBe("all");
    expect((screen.getByLabelText("Buscar oportunidades de equipos") as HTMLInputElement).value).toBe("");
  });

  it("shows triage-specific empty copy when triage filter has no matches", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("Filtrar oportunidades de equipos por triaje"), {
      target: { value: "missing_contact" },
    });

    screen.getByText(/No hay oportunidades con este filtro de triage/);
    expect(screen.queryByText("Universidad Ejemplo")).toBeNull();
  });

  it("filters by search and shows no-match message", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[row]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Buscar oportunidades de equipos"), {
      target: { value: "zzznomatch" },
    });
    screen.getByText(/Ninguna oportunidad coincide con la búsqueda actual/);
    expect(screen.queryByText("Universidad Ejemplo")).toBeNull();
  });

  it("does not invent contact email when field is empty", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[{ ...row, contact_email: "" }]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: null,
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /@/ })).toBeNull();
  });

  it("shows unavailable panel when feed is in reduced mode", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: true,
          note: "Cola equipment_first no encontrada.",
          count: 0,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    const panel = screen.getByTestId("equipment-feed-unavailable");
    screen.getByText("Fuente de licitaciones no disponible");
    expect(panel.textContent).toMatch(/No significa que no existan oportunidades/);
    expect(panel.textContent).toMatch(/equipment_first_operator_queue/);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows zero-items message when feed is available but empty", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="sqlite"
        items={[]}
        meta={{
          data_source: "active_current_csv",
          reduced_mode: false,
          note: "",
          count: 0,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    screen.getByText("No hay oportunidades de equipos en la cola actual.");
    expect(screen.queryByTestId("equipment-feed-unavailable")).toBeNull();
  });

  it("renders ChileCompra equipment labels in Spanish", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="postgres"
        items={[
          {
            ...row,
            codigo_licitacion: "1051-1-LP26",
            equipment_category: "centrifuge; homogenizer",
            next_action: "contact_after_close",
            safe_channel: "mercado_publico_only",
            contact_status: "review_required",
          },
        ]}
        meta={{
          data_source: "postgres_mirror",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    screen.getByText("Centrífuga; Homogeneizador / agitador");
    screen.getByText("Revisar después del cierre");
    screen.getByText("Mercado Público");
    screen.getByText("Revisión requerida");
  });

  it("formats ISO close date and shows ChileCompra detail", () => {
    render(
      <EquipmentOpportunitiesTable
        backend="postgres"
        items={[
          {
            ...row,
            codigo_licitacion: "1051-1-LP26",
            close_date: "2026-06-17T19:00:00",
            fecha_publicacion: "10/06/2026 08:00:00",
            mercado_publico_url:
              "https://www.mercadopublico.cl/BuscarLicitacion?codigoLicitacion=1051-1-LP26",
            unspsc_code: "41100000",
            cantidad: "2",
            unidad: "Unidad",
            producto: "Centrifuga refrigerada",
            nivel_1: "Equipamiento",
            chilecompra_status: "Publicada",
            validity_status: "open",
          },
        ]}
        meta={{
          data_source: "postgres_mirror",
          reduced_mode: false,
          note: "",
          count: 1,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );

    expect(screen.queryByText("2026-06-17T19:00:00")).toBeNull();
    screen.getByRole("link", { name: "Buscar en Mercado Público" });
    expect(screen.getByRole("link", { name: "Buscar en Mercado Público" }).getAttribute("href")).toContain(
      "1051-1-LP26",
    );
    expect(screen.getByRole("link", { name: "Buscar en Mercado Público" }).getAttribute("href")).not.toMatch(
      /ticket/i,
    );

    fireEvent.click(screen.getByRole("button", { name: "Ver detalle de licitación 1051-1-LP26" }));
    const drawer = screen.getByTestId("equipment-opportunity-detail-drawer");
    within(drawer).getByText("Centrifuga refrigerada");
    within(drawer).getByText("41100000");
  });

  const detailRow: EquipmentOpportunityItem = {
    ...row,
    codigo_licitacion: "1051-1-LP26",
    title: "Adquisición centrifuga laboratorio",
    close_date: "2026-06-17T19:00:00",
    close_at: "2026-06-17T19:00:00-04:00",
    fecha_publicacion: "10/06/2026 08:00:00",
    mercado_publico_url:
      "https://www.mercadopublico.cl/BuscarLicitacion?codigoLicitacion=1051-1-LP26",
    item_description:
      "Centrifuga refrigerada de alta velocidad con rotor incluido para laboratorio clínico",
    operator_note: "fit=90 | source:chilecompra_api | revisar pliego",
    unspsc_code: "41100000",
    cantidad: "2",
    unidad: "Unidad",
    producto: "Centrifuga refrigerada",
    nivel_1: "Equipamiento médico",
    chilecompra_status: "Publicada",
    chilecompra_status_code: "5",
    validity_status: "open",
    source: "chilecompra_api",
    api_checked_at_utc: "2026-06-14T12:00:00+00:00",
  };

  function renderDetailTable(items: EquipmentOpportunityItem[] = [detailRow]) {
    return render(
      <EquipmentOpportunitiesTable
        backend="postgres"
        items={items}
        meta={{
          data_source: "postgres_mirror",
          reduced_mode: false,
          note: "",
          count: items.length,
          campaign_mode: "equipment_first",
        }}
        loading={false}
        error={null}
        onRetry={() => {}}
        onContactSelect={() => {}}
      />,
    );
  }

  it("opens detail drawer when licitation code is clicked", () => {
    renderDetailTable();
    expect(screen.queryByTestId("equipment-opportunity-detail-drawer")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Ver detalle de licitación 1051-1-LP26" }),
    );
    expect(screen.getByTestId("equipment-opportunity-detail-drawer")).toBeTruthy();
  });

  it("drawer shows full opportunity detail and formatted dates", () => {
    renderDetailTable();
    fireEvent.click(
      screen.getByRole("button", { name: "Ver detalle de licitación 1051-1-LP26" }),
    );
    const drawer = screen.getByTestId("equipment-opportunity-detail-drawer");
    expect(drawer.textContent).toContain("Universidad Ejemplo");
    expect(drawer.textContent).toContain("1051-1-LP26");
    expect(
      within(drawer)
        .getAllByTestId("equipment-triage-badge")
        .some((el) => el.textContent === "Cotizar ahora"),
    ).toBe(true);
    expect(drawer.textContent).toContain("Adquisición centrifuga laboratorio");
    expect(drawer.textContent).toContain(
      "Centrifuga refrigerada de alta velocidad con rotor incluido para laboratorio clínico",
    );
    expect(drawer.textContent).toContain("fit=90 | source:chilecompra_api | revisar pliego");
    expect(drawer.textContent).toMatch(/Fecha cierre/i);
    expect(drawer.textContent).toMatch(/17 jun 2026, 19:00/);
    expect(drawer.textContent).toMatch(/Fecha publicación/i);
    expect(drawer.textContent).toContain("Cantidad");
    expect(drawer.textContent).toContain("41100000");
    expect(drawer.textContent).toContain("Equipamiento médico");
    expect(within(drawer).getByRole("link", { name: "Buscar en Mercado Público" })).toBeTruthy();
  });

  it("drawer does not render unsafe Mercado Público ticket URLs", () => {
    renderDetailTable([
      {
        ...detailRow,
        mercado_publico_url:
          "https://api.chilecompra.cl/...?ticket=SECRET-TICKET-UUID",
      },
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Ver detalle de licitación 1051-1-LP26" }),
    );
    const drawer = screen.getByTestId("equipment-opportunity-detail-drawer");
    expect(drawer.querySelector("a[href*='ticket']")).toBeNull();
    expect(drawer.textContent).not.toMatch(/SECRET-TICKET-UUID/);
  });

  it("close button hides the detail drawer", () => {
    renderDetailTable();
    fireEvent.click(
      screen.getByRole("button", { name: "Ver detalle de licitación 1051-1-LP26" }),
    );
    const drawer = screen.getByTestId("equipment-opportunity-detail-drawer");
    fireEvent.click(within(drawer).getByRole("button", { name: "Cerrar detalle de licitación" }));
    expect(screen.queryByTestId("equipment-opportunity-detail-drawer")).toBeNull();
  });

  it("drawer shows attachment list when anexos are present", () => {
    renderDetailTable([
      {
        ...detailRow,
        anexos: [
          {
            nombre: "Bases técnicas.pdf",
            tipo: "Bases",
            descripcion: "Especificaciones del equipo",
            tamano: "1.2 MB",
            fecha_adjunto: "01/06/2026",
            url: "https://www.mercadopublico.cl/archivos/bases.pdf",
          },
        ],
      },
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Ver detalle de licitación 1051-1-LP26" }),
    );
    const drawer = screen.getByTestId("equipment-opportunity-detail-drawer");
    expect(drawer.textContent).toContain("Adjuntos / bases");
    expect(drawer.textContent).toContain("Bases técnicas.pdf");
    expect(drawer.textContent).toContain("1.2 MB");
    expect(within(drawer).getByRole("link", { name: "Abrir adjunto" })).toBeTruthy();
  });

  it("drawer shows Mercado Público fallback when anexos are empty", () => {
    renderDetailTable();
    fireEvent.click(
      screen.getByRole("button", { name: "Ver detalle de licitación 1051-1-LP26" }),
    );
    const drawer = screen.getByTestId("equipment-opportunity-detail-drawer");
    expect(drawer.textContent).toContain("Adjuntos / bases");
    expect(drawer.textContent).toContain(
      "Adjuntos disponibles en Mercado Público; abrir la licitación para ver anexos.",
    );
    expect(within(drawer).getAllByRole("link", { name: "Buscar en Mercado Público" })).toHaveLength(1);
  });
});

