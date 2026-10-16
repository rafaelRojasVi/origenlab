import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { OpportunityCardData, QuoteContents, RevisionCard } from "../crmTypes";
import { CaseLinks, CaseMoneySummary, QuoteList } from "./CaseQuotes";

// Every value below is invented; the repository is public.
const CONTENTS: QuoteContents = {
  currency: "CLP",
  net_total: "1000000",
  net_source: "document",
  tax_total: "190000",
  gross_total: "1190000",
  lines: [
    { line_no: 1, item: "1", parent_item: null, kind: "equipment", brand: "Marca Ficticia", model: "XF-200",
      description: "Equipo ficticio de laboratorio", qty: "1.000000", unit_price: "950000.0000",
      line_total: "950000.0000", optional: false, check_status: "verified" },
    { line_no: 2, item: "2", parent_item: null, kind: "accessory", brand: null, model: null,
      description: "Accesorio ficticio", qty: "2.000000", unit_price: "25000.0000",
      line_total: "50000.0000", optional: false, check_status: "verified" },
    { line_no: 3, item: "3", parent_item: null, kind: "service", brand: null, model: null,
      description: "Instalación opcional", qty: null, unit_price: null,
      line_total: "80000.0000", optional: true, check_status: "single_source" },
  ],
};

function rev(over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: "r-2", revision_no: 2, status: "sent", origin: "historical_import", sent_at: "2026-09-20T12:00:00Z",
    superseded_by_revision_no: null, is_active: true, document: null,
    gmail: { source_record_id: "s", message_id: "m", thread_id: "t", url: "https://mail.google.com/mail/u/0/#all/m2", subject: "x" },
    drive: { file_url: "https://drive.google.com/file/d/f2/view", folder_url: null, original_filename: "x.pdf" } as RevisionCard["drive"],
    quote_number: "00999-26", contents: CONTENTS, ...over,
  };
}

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const older = rev({ revision_id: "r-1", revision_no: 1, is_active: false, superseded_by_revision_no: 2, drive: null,
    contents: { ...CONTENTS, net_total: "1200000", tax_total: "228000", gross_total: "1428000" } });
  const current = rev();
  return {
    opportunity_id: "case-1", title: "Caso ficticio", stage: "quoting", version: 1, created_at: null, updated_at: null,
    closed_at: null, close_reason: null,
    organization: { organization_id: "org-1", name: "Universidad Ficticia", confirmation: "confirmed", version: 1 },
    other_organizations: [], contact: null,
    quotes: [{ quote_id: "q-1", quote_number: "00999-26", number_origin: "printed_historical", revisions: [older, current] }],
    quote_numbers: ["00999-26"], revision_count: 2, latest_revision: current,
    money: [{ currency: "CLP", net_total: "1000000", quote_number: "00999-26", revision_no: 2, sent_at: "2026-09-20T12:00:00Z", quote_count: 1 }],
    drive_folder: { url: "https://drive.google.com/drive/folders/d1" } as OpportunityCardData["drive_folder"],
    attention: [], status: "ok",
    next_action: { text: "x", source: "suggested", due_at: null },
    ...over,
  } as OpportunityCardData;
}

describe("case panel money and quotations", () => {
  it("shows the quotation's neto large with IVA, total and when it was sent", () => {
    render(<CaseMoneySummary card={card()} />);
    const box = screen.getByTestId("case-money");
    expect(box).toHaveTextContent("Cotizado, neto");
    expect(box).toHaveTextContent("00999-26");
    expect(box).toHaveTextContent(/20 sept 2026, \d{2}:\d{2}/);
    expect(box).toHaveTextContent("$ 1.000.000");
    expect(box).toHaveTextContent("IVA $ 190.000");
    expect(box).toHaveTextContent("Total $ 1.190.000");
  });

  it("says the last quotation was not read instead of inventing a number", () => {
    render(<CaseMoneySummary card={card({ money: [{ currency: "unread", net_total: "1", quote_number: "00999-26", revision_no: 2, sent_at: "2026-09-20T12:00:00Z", quote_count: 1 }] })} />);
    expect(screen.getByTestId("case-money")).toHaveTextContent("La cotización 00999-26 aún no se ha leído.");
  });

  it("lists the vigente revision's equipment with prices and folds the older revision", () => {
    render(<QuoteList card={card()} />);
    const block = screen.getByTestId("quote-block");
    expect(within(block).getByText("XF-200 · Equipo ficticio de laboratorio")).toBeInTheDocument();
    expect(block).not.toHaveTextContent("1 × $ 950.000"); // a single unit shows only its total
    expect(block).toHaveTextContent("2 × $ 25.000");
    expect(within(block).getByText("Opcional")).toBeInTheDocument();
    expect(block).toHaveTextContent("IVA 19%");
    expect(within(block).getByLabelText("Abrir PDF r2 en Drive")).toHaveAttribute("href", "https://drive.google.com/file/d/f2/view");
    expect(within(block).getByText("1 revisión anterior")).toBeInTheDocument();
    expect(block).toHaveTextContent("Reemplazada por r2");
  });

  it("asks to open an unread PDF and shows Gmail, the folder and the vigente PDF at the top", () => {
    const unread = card({ quotes: [{ quote_id: "q-1", quote_number: "00999-26", number_origin: "x", revisions: [rev({ contents: null })] }] });
    render(<><CaseLinks card={unread} /><QuoteList card={unread} /></>);
    expect(screen.getByText("PDF aún no leído.")).toBeInTheDocument();
    const links = screen.getByTestId("case-links");
    expect(within(links).getByRole("link", { name: /carpeta del caso en Drive/ })).toHaveAttribute("href", "https://drive.google.com/drive/folders/d1");
    expect(within(links).getByRole("link", { name: /PDF 00999-26 r2/ })).toHaveAttribute("href", "https://drive.google.com/file/d/f2/view");
    expect(within(links).getByRole("link", { name: /correo del caso en Gmail/ }).getAttribute("href")).toContain("authuser=contacto");
  });

  it("shows every line on request when a quotation has more than eight", () => {
    const many = { ...CONTENTS, lines: Array.from({ length: 11 }, (_, i) => ({ ...CONTENTS.lines[0], line_no: i + 1, model: `M-${i + 1}` })) };
    render(<QuoteList card={card({ quotes: [{ quote_id: "q", quote_number: "00999-26", number_origin: "x", revisions: [rev({ contents: many })] }] })} />);
    expect(screen.queryByText(/^M-11/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ver 3 líneas más" }));
    expect(screen.getByText(/^M-11/)).toBeInTheDocument();
  });
});


describe("several quotations on one case", () => {
  // Owner 2026-10-12: the top shows the LAST quotation sent, never a sum; when matters.
  const three = () => card({
    quotes: [
      { quote_id: "q-1", quote_number: "00997-26", number_origin: "x", revisions: [rev({ revision_id: "a", quote_number: "00997-26", sent_at: "2026-09-07T12:00:00Z" })] },
      { quote_id: "q-2", quote_number: "00998-26", number_origin: "x", revisions: [rev({ revision_id: "b", quote_number: "00998-26", sent_at: "2026-09-22T12:00:00Z",
        contents: { ...CONTENTS, net_total: "480000", tax_total: "91200", gross_total: "571200" },
        drive: { file_url: "https://drive.google.com/file/d/f98/view", folder_url: null, original_filename: "y.pdf" } as RevisionCard["drive"] })] },
      { quote_id: "q-3", quote_number: "00999-26", number_origin: "x", revisions: [rev({ revision_id: "c", sent_at: "2026-09-09T12:00:00Z" })] },
    ],
    money: [{ currency: "CLP", net_total: "480000", quote_number: "00998-26", revision_no: 2, sent_at: "2026-09-22T12:00:00Z", quote_count: 3 }],
  });

  it("shows the last quotation sent, not a sum, and says the others are not added", () => {
    render(<CaseMoneySummary card={three()} />);
    const box = screen.getByTestId("case-money");
    expect(box).toHaveTextContent("Última cotización enviada");
    expect(box).toHaveTextContent("00998-26");
    expect(box).toHaveTextContent("$ 480.000");
    expect(box).toHaveTextContent("IVA $ 91.200");
    expect(box).toHaveTextContent("Las otras 2 cotizaciones del caso no se suman.");
    expect(box).not.toHaveTextContent("Suma");
  });

  it("lists the newest quotation first, open and marked, and the others folded", () => {
    render(<QuoteList card={three()} />);
    const blocks = screen.getAllByTestId("quote-block");
    expect(blocks.map((b) => b.querySelector(".font-mono")?.textContent)).toEqual(["00998-26", "00999-26", "00997-26"]);
    expect(within(blocks[0]).getByText("Última")).toBeInTheDocument();
    expect(within(blocks[0]).getByRole("button", { name: "Contraer cotización 00998-26" })).toHaveAttribute("aria-expanded", "true");
    const toggle = within(blocks[1]).getByRole("button", { name: "Expandir cotización 00999-26" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(within(blocks[1]).getByRole("button", { name: "Contraer cotización 00999-26" })).toHaveAttribute("aria-expanded", "true");
  });

  it("links the last quotation's PDF at the top", () => {
    render(<CaseLinks card={three()} />);
    expect(within(screen.getByTestId("case-links")).getByRole("link", { name: /PDF 00998-26 r2/ }))
      .toHaveAttribute("href", "https://drive.google.com/file/d/f98/view");
  });
});

describe("«Abrir en Cotizador»", () => {
  const authored = { quote_id: "q-a", quote_number: "00042-26", number_origin: "minted",
    revisions: [rev({ revision_id: "r-a", revision_no: 1, status: "approved", origin: "authored", quote_number: "00042-26" })] };

  it("links a quote of this app to the Cotizador, never a historical one", () => {
    render(<QuoteList card={card({ quotes: [...card().quotes, authored] })} mayOpenCotizador />);
    const links = screen.getAllByRole("link", { name: /Abrir en Cotizador/ });
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["#/crm/cotizador/q-a"]);
  });

  it("is not offered to someone who may not open the Cotizador", () => {
    render(<QuoteList card={card({ quotes: [authored] })} />);
    expect(screen.queryByRole("link", { name: /Abrir en Cotizador/ })).not.toBeInTheDocument();
  });
});
