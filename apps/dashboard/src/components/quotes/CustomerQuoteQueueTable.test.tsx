import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CustomerQuoteQueueTable } from "./CustomerQuoteQueueTable";
import {
  drivePendingQuoteItemFixture,
  globalQuoteItemFixture,
} from "../../test/fixtures/customerQuoteFixtures";
import type { QuoteQueueRow } from "../../api/customerQuoteTypes";

describe("CustomerQuoteQueueTable", () => {
  it("renders a CRM row and opens the drawer on quote-number click", () => {
    const item = globalQuoteItemFixture();
    const onOpenQuote = vi.fn();
    const rows: QuoteQueueRow[] = [{ kind: "crm", item }];

    render(<CustomerQuoteQueueTable rows={rows} onOpenQuote={onOpenQuote} />);

    fireEvent.click(screen.getByRole("button", { name: item.quote.quote_number }));
    expect(onOpenQuote).toHaveBeenCalledWith(item);
  });

  it("renders a Drive-only pending row with folder name, badges, identifier and modified date", () => {
    const item = drivePendingQuoteItemFixture({
      folder_name: "CN01191-ICN Chile",
      document_identifier: "CN01191",
    });
    const rows: QuoteQueueRow[] = [{ kind: "drive_pending", item }];

    render(<CustomerQuoteQueueTable rows={rows} onOpenQuote={vi.fn()} />);

    expect(screen.getByText("CN01191-ICN Chile")).toBeInTheDocument();
    expect(screen.getByText("CN01191")).toBeInTheDocument();
    expect(screen.getByText("Pendiente en Drive")).toBeInTheDocument();
    expect(screen.getByText("Sin registro CRM")).toBeInTheDocument();
  });

  it("gives a Drive-only row an 'Abrir carpeta' primary action that never opens the CRM drawer", () => {
    const item = drivePendingQuoteItemFixture();
    const onOpenQuote = vi.fn();
    const rows: QuoteQueueRow[] = [{ kind: "drive_pending", item }];

    render(<CustomerQuoteQueueTable rows={rows} onOpenQuote={onOpenQuote} />);

    const link = screen.getByRole("link", { name: "Abrir carpeta" });
    expect(link).toHaveAttribute("href", item.folder_web_url);

    fireEvent.click(link);
    expect(onOpenQuote).not.toHaveBeenCalled();
  });

  it("never exposes CRM-only actions or fabricated CRM fields on a Drive-only row", () => {
    const item = drivePendingQuoteItemFixture();
    const rows: QuoteQueueRow[] = [{ kind: "drive_pending", item }];

    render(<CustomerQuoteQueueTable rows={rows} onOpenQuote={vi.fn()} />);

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/reintentar/i)).toBeNull();
    expect(screen.queryByText("Borrador")).toBeNull();
    expect(screen.queryByText(/drive listo|aprovisionando|error de drive/i)).toBeNull();
  });
});
