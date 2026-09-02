import "@testing-library/jest-dom";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuoteDetailDrawer } from "./QuoteDetailDrawer";
import * as client from "../../api/customerQuoteClient";
import { globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";

vi.mock("../../api/customerQuoteClient");

describe("QuoteDetailDrawer", () => {
  it("refreshes the quote on open and shows identity, opportunity and Drive links", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open onClose={vi.fn()} onOpenVentas={vi.fn()} />);

    await waitFor(() => expect(client.fetchCustomerQuote).toHaveBeenCalledWith(fixture.quote.quote_id));
    screen.getByText("CEAF");
    screen.getByText("01183-26");
    screen.getByText("CN01183");
    screen.getByText("Centrífuga CEAF");
    expect(screen.getByRole("link", { name: /Abrir carpeta/ })).toHaveAttribute(
      "href",
      "https://drive.google.com/drive/folders/f1",
    );
  });

  it("shows the failure category and a retry action for a failed workspace, reusing the retry command", async () => {
    const base = globalQuoteItemFixture();
    const failed = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: {
          ...base.quote.drive_workspace,
          provisioning_status: "failed",
          failure_category: "drive_unavailable",
          retryable: true,
        },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: failed.quote });
    vi.mocked(client.retryCustomerQuoteDriveWorkspace).mockResolvedValue({
      ...failed.quote,
      drive_workspace: { ...failed.quote.drive_workspace, provisioning_status: "ready" },
    });

    render(<QuoteDetailDrawer item={failed} open onClose={vi.fn()} onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText(/Google Drive no está disponible/));

    fireEvent.click(screen.getByRole("button", { name: /Reintentar/ }));
    await waitFor(() =>
      expect(client.retryCustomerQuoteDriveWorkspace).toHaveBeenCalledWith(failed.quote.quote_id, {
        expected_version: failed.quote.drive_workspace.version,
      }),
    );
  });

  it("renders a pending workspace as provisioning language, never as a failure", async () => {
    const base = globalQuoteItemFixture();
    const pending = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: {
          ...base.quote.drive_workspace,
          provisioning_status: "pending",
          retryable: false,
        },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: pending.quote });

    render(<QuoteDetailDrawer item={pending} open onClose={vi.fn()} onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText(/Preparando carpeta en Drive/));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/no se pudo crear/)).toBeNull();
  });

  it("never renders a raw Drive id as a link — only server-validated https URLs", async () => {
    const base = globalQuoteItemFixture();
    const noLinks = globalQuoteItemFixture({
      quote: {
        ...base.quote,
        drive_workspace: { ...base.quote.drive_workspace, folder_web_url: null, sheet_web_url: null },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: noLinks.quote });

    render(<QuoteDetailDrawer item={noLinks} open onClose={vi.fn()} onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("01183-26"));
    expect(screen.queryByRole("link", { name: /Abrir carpeta/ })).toBeNull();
  });

  it("'Ver en Ventas' calls onOpenVentas with the exact durable opportunity id", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onOpenVentas = vi.fn();

    render(<QuoteDetailDrawer item={fixture} open onClose={vi.fn()} onOpenVentas={onOpenVentas} />);
    await waitFor(() => screen.getByText("01183-26"));

    fireEvent.click(screen.getByRole("button", { name: "Ver en Ventas" }));
    expect(onOpenVentas).toHaveBeenCalledWith(fixture.quote.sales_opportunity_id);
    expect(onOpenVentas).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when item is null", () => {
    render(<QuoteDetailDrawer item={null} open={false} onClose={vi.fn()} onOpenVentas={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
