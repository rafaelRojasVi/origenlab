import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { TenderAnnexPreview } from "../../api/institutionIntel/types";
import { TenderAnnexUploadPanel } from "./TenderAnnexUploadPanel";

const previewTenderAnnexBundle = vi.fn();
const importTenderAnnexBundle = vi.fn();
const getTenderAttachmentNavigation = vi.fn();

vi.mock("../../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    previewTenderAnnexBundle: (...args: unknown[]) => previewTenderAnnexBundle(...args),
    importTenderAnnexBundle: (...args: unknown[]) =>
      importTenderAnnexBundle(...args),
    getTenderAttachmentNavigation: (...args: unknown[]) =>
      getTenderAttachmentNavigation(...args),
  },
}));

vi.mock("../../api/operatorClient", async () => {
  const actual = await vi.importActual<typeof import("../../api/operatorClient")>("../../api/operatorClient");
  return actual;
});

import { OperatorApiError } from "../../api/operatorClient";

function zipFile(name = "anexos.zip", size = 1024): File {
  const bytes = new Uint8Array(size);
  const file = new File([bytes], name, { type: "application/zip" });
  return file;
}

const BASE_PREVIEW: TenderAnnexPreview = {
  tenderCode: "2410-66-LP26",
  acquisition: {
    source: "operator_complete_bundle",
    completenessState: "unknown",
    completenessReason: "operator_did_not_declare_complete",
    operatorDeclaredComplete: false,
  },
  archive: {
    sha256: "a".repeat(64),
    attachmentsDiscovered: 3,
    attachmentsDownloaded: 3,
    rejectedEntries: [],
  },
  bundleComplete: false,
  incompleteReasonCodes: ["operator_completeness_not_declared"],
  licitacionIntel: {
    tenderCode: "2410-66-LP26",
    buyerDisplayName: "",
    eligibilityStatus: "unknown",
    procurementMethodRaw: null,
    t1SourceKind: null,
    terms: {
      status: "available",
      data: [
        {
          fieldName: "payment_deadline_days",
          label: "payment_deadline_days",
          state: "explicit",
          value: 30,
          unit: "días",
          evidence: {
            excerpt: "Los pagos serán realizados dentro de los 30 días corridos",
            documentLabel: "bases.pdf",
            locator: "página 3",
          },
        },
      ],
    },
    itemBudget: { status: "available_empty" },
    totalBudgetReconciled: false,
    recognitionDelta: { status: "not_available" },
    coverage: { status: "available", data: { documentsDiscovered: 3, documentsRead: 3, incompleteReasonCodes: [] } },
  },
  published: false,
  persisted: false,
  contactAuthorization: false,
  outreachAuthorization: false,
};

describe("TenderAnnexUploadPanel", () => {
  it("accepts a dropped .zip file and shows its name", () => {
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    const dropZone = screen.getByTestId("annex-drop-zone");
    const file = zipFile("expediente.zip");

    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } });

    screen.getByText("expediente.zip");
    expect(screen.queryByTestId("annex-client-error")).toBeNull();
  });

  it("rejects a dropped file with the wrong extension", () => {
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    const dropZone = screen.getByTestId("annex-drop-zone");
    const file = new File([new Uint8Array(10)], "documento.pdf", { type: "application/pdf" });

    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } });

    screen.getByTestId("annex-client-error");
    expect(screen.queryByTestId("annex-selected-file")).toBeNull();
  });

  it("rejects an oversized file before uploading", () => {
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    const dropZone = screen.getByTestId("annex-drop-zone");
    const file = zipFile("grande.zip", 70 * 1024 * 1024);

    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } });

    screen.getByTestId("annex-client-error");
    expect(screen.queryByTestId("annex-selected-file")).toBeNull();
    expect(previewTenderAnnexBundle).not.toHaveBeenCalled();
  });

  it("completeness checkbox defaults to unchecked", () => {
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    const checkbox = screen.getByTestId("annex-declare-complete-checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
  });

  it("does not auto-submit on drop -- Procesar documentos must be clicked explicitly", () => {
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    const dropZone = screen.getByTestId("annex-drop-zone");
    fireEvent.drop(dropZone, { dataTransfer: { files: [zipFile()] } });

    expect(previewTenderAnnexBundle).not.toHaveBeenCalled();
  });

  it("sends declareComplete:false by default and true when the checkbox is checked", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockResolvedValue(BASE_PREVIEW);
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);

    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => expect(previewTenderAnnexBundle).toHaveBeenCalledTimes(1));
    expect(previewTenderAnnexBundle.mock.calls[0][2]).toEqual({ declareComplete: false });

    fireEvent.click(screen.getByTestId("annex-declare-complete-checkbox"));
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => expect(previewTenderAnnexBundle).toHaveBeenCalledTimes(2));
    expect(previewTenderAnnexBundle.mock.calls[1][2]).toEqual({ declareComplete: true });
  });

  it("shows a processing state while the request is in flight", async () => {
    previewTenderAnnexBundle.mockReset();
    let resolvePromise: (value: TenderAnnexPreview) => void = () => {};
    previewTenderAnnexBundle.mockReturnValue(
      new Promise<TenderAnnexPreview>((resolve) => {
        resolvePromise = resolve;
      }),
    );
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => {
      expect((screen.getByTestId("annex-process-button") as HTMLButtonElement).disabled).toBe(true);
    });
    screen.getByText("Procesando…");

    resolvePromise(BASE_PREVIEW);
    await waitFor(() => screen.getByTestId("annex-preview-result"));
  });

  it("renders a distinct error message on server rejection (422)", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockRejectedValue(new OperatorApiError("bad zip", 422));
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => screen.getByTestId("annex-server-error"));
    screen.getByText(/rechazado/);
    expect(screen.queryByTestId("annex-preview-result")).toBeNull();
  });

  it("renders the successful imported preview with banner, summary, and T1 facts/evidence", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockResolvedValue(BASE_PREVIEW);
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => screen.getByTestId("annex-preview-result"));

    screen.getByText("Vista previa — aún no guardada");
    screen.getByTestId("annex-preview-summary");
    // T1 fact rendered via the reused LicitacionIntelBody.
    screen.getByText("payment_deadline_days");
    // Evidence reachable via the same disclosure component published data uses.
    screen.getByText(/Ver evidencia/);
  });

  it("renders 'No confirmada' coverage label when completeness is unknown", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockResolvedValue(BASE_PREVIEW);
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => screen.getByTestId("annex-preview-summary"));
    expect(screen.getByTestId("annex-preview-coverage-label").textContent).toContain("No confirmada");
  });

  it("renders 'Completa — Declarada por operador' when the operator declared complete and extraction fully succeeded", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockResolvedValue({
      ...BASE_PREVIEW,
      acquisition: {
        ...BASE_PREVIEW.acquisition,
        completenessState: "complete",
        operatorDeclaredComplete: true,
      },
      bundleComplete: true,
    } satisfies TenderAnnexPreview);
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => screen.getByTestId("annex-preview-summary"));
    const text = screen.getByTestId("annex-preview-coverage-label").textContent;
    expect(text).toContain("Completa");
    expect(text).toContain("Declarada por operador");
  });

  it("renders rejected ZIP entries when present", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockResolvedValue({
      ...BASE_PREVIEW,
      archive: { ...BASE_PREVIEW.archive, rejectedEntries: ["'../escape.txt': unsafe_member_path"] },
    } satisfies TenderAnnexPreview);
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => screen.getByTestId("annex-preview-rejected-entries"));
    screen.getByText(/unsafe_member_path/);
  });

  it("never renders a publish control", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockResolvedValue(BASE_PREVIEW);
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() => screen.getByTestId("annex-preview-result"));
    expect(screen.queryByRole("button", { name: /publicar/i })).toBeNull();
    expect(screen.queryByText(/publicar/i)).toBeNull();
  });

  it("does not persist on preview; Guardar expediente is a separate explicit action", async () => {
    previewTenderAnnexBundle.mockReset();
    importTenderAnnexBundle.mockReset();

    previewTenderAnnexBundle.mockResolvedValue(BASE_PREVIEW);
    importTenderAnnexBundle.mockResolvedValue({
      ...BASE_PREVIEW,
      published: true,
      persisted: true,
    });

    const onPersisted = vi.fn();

    render(
      <TenderAnnexUploadPanel
        tenderCode="2410-66-LP26"
        onPersisted={onPersisted}
      />,
    );

    const file = zipFile("expediente.zip");
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), {
      dataTransfer: { files: [file] },
    });

    fireEvent.click(screen.getByTestId("annex-process-button"));

    await waitFor(() =>
      screen.getByTestId("annex-preview-result"),
    );

    expect(importTenderAnnexBundle).not.toHaveBeenCalled();
    screen.getByText("Vista previa — aún no guardada");

    fireEvent.click(screen.getByTestId("annex-save-button"));

    await waitFor(() =>
      expect(importTenderAnnexBundle).toHaveBeenCalledWith(
        "2410-66-LP26",
        file,
        { declareComplete: false },
      ),
    );

    await waitFor(() =>
      expect(onPersisted).toHaveBeenCalledTimes(1),
    );
  });

  it("shows server-backed Expediente guardado after a browser-style reload", () => {
    render(
      <TenderAnnexUploadPanel
        tenderCode="2410-66-LP26"
        persistedOperatorImport
      />,
    );

    screen.getByTestId("annex-persisted-banner");
    screen.getByText("Expediente guardado");

    expect(
      screen.queryByTestId("annex-preview-result"),
    ).toBeNull();
  });

  it("removing the selection clears the file and any prior result", async () => {
    previewTenderAnnexBundle.mockReset();
    previewTenderAnnexBundle.mockResolvedValue(BASE_PREVIEW);
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.drop(screen.getByTestId("annex-drop-zone"), { dataTransfer: { files: [zipFile()] } });
    fireEvent.click(screen.getByTestId("annex-process-button"));
    await waitFor(() => screen.getByTestId("annex-preview-result"));

    fireEvent.click(screen.getByTestId("annex-remove-file"));

    expect(screen.queryByTestId("annex-selected-file")).toBeNull();
    expect(screen.queryByTestId("annex-preview-result")).toBeNull();
    expect((screen.getByTestId("annex-process-button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders Mercado Público navigation before the ZIP upload workflow", () => {
    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);

    const navigationButton = screen.getByTestId("attachment-navigation-button");
    const dropZone = screen.getByTestId("annex-drop-zone");

    expect(
      navigationButton.compareDocumentPosition(dropZone) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
  });

  it("opens a blank tab synchronously and navigates it to the resolved attachment URL", async () => {
    previewTenderAnnexBundle.mockReset();
    getTenderAttachmentNavigation.mockReset();

    const replace = vi.fn();
    const close = vi.fn();
    const popup = {
      opener: {},
      location: { replace },
      close,
    } as unknown as Window;
    const openSpy = vi.spyOn(window, "open").mockReturnValue(popup);

    getTenderAttachmentNavigation.mockResolvedValue({
      tenderCode: "2410-66-LP26",
      destinationKind: "attachments",
      url:
        "https://www.mercadopublico.cl/Procurement/Modules/" +
        "Attachment/ViewAttachmentLC.aspx?enc=EPHEMERAL123",
      ephemeral: true,
    });

    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.click(screen.getByTestId("attachment-navigation-button"));

    expect(openSpy).toHaveBeenCalledWith("about:blank", "_blank");

    await waitFor(() =>
      expect(getTenderAttachmentNavigation).toHaveBeenCalledWith("2410-66-LP26"),
    );
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(
        expect.stringContaining("ViewAttachmentLC.aspx?enc=EPHEMERAL123"),
      ),
    );

    expect(close).not.toHaveBeenCalled();
    expect(previewTenderAnnexBundle).not.toHaveBeenCalled();
    expect(screen.queryByText(/EPHEMERAL123/)).toBeNull();

    openSpy.mockRestore();
  });

  it("uses the backend tender-page fallback exactly as returned", async () => {
    getTenderAttachmentNavigation.mockReset();

    const replace = vi.fn();
    const popup = {
      opener: {},
      location: { replace },
      close: vi.fn(),
    } as unknown as Window;
    const openSpy = vi.spyOn(window, "open").mockReturnValue(popup);

    const fallback =
      "https://www.mercadopublico.cl/Procurement/Modules/" +
      "RFB/DetailsAcquisition.aspx?idlicitacion=2410-66-LP26";

    getTenderAttachmentNavigation.mockResolvedValue({
      tenderCode: "2410-66-LP26",
      destinationKind: "tender",
      url: fallback,
      ephemeral: true,
    });

    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.click(screen.getByTestId("attachment-navigation-button"));

    await waitFor(() => expect(replace).toHaveBeenCalledWith(fallback));

    openSpy.mockRestore();
  });

  it("shows a local error and never calls the resolver when the popup is blocked", () => {
    getTenderAttachmentNavigation.mockReset();
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);

    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.click(screen.getByTestId("attachment-navigation-button"));

    expect(getTenderAttachmentNavigation).not.toHaveBeenCalled();
    expect(screen.getByTestId("attachment-navigation-error").textContent).toContain(
      "bloqueó",
    );

    openSpy.mockRestore();
  });

  it("closes the blank tab and shows an error when navigation resolution fails", async () => {
    getTenderAttachmentNavigation.mockReset();

    const replace = vi.fn();
    const close = vi.fn();
    const popup = {
      opener: {},
      location: { replace },
      close,
    } as unknown as Window;
    const openSpy = vi.spyOn(window, "open").mockReturnValue(popup);

    getTenderAttachmentNavigation.mockRejectedValue(
      new OperatorApiError("unavailable", 503),
    );

    render(<TenderAnnexUploadPanel tenderCode="2410-66-LP26" />);
    fireEvent.click(screen.getByTestId("attachment-navigation-button"));

    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByTestId("attachment-navigation-error").textContent).toContain(
      "no está disponible",
    );

    openSpy.mockRestore();
  });

});
