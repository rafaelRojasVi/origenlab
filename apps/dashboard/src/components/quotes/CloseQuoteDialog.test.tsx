import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CloseQuoteDialog } from "./CloseQuoteDialog";
import * as quoteClient from "../../api/customerQuoteClient";
import { OperatorApiError } from "../../api/operatorClient";
import { globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";

vi.mock("../../api/customerQuoteClient");

function sentItem() {
  return globalQuoteItemFixture({
    quote: {
      ...globalQuoteItemFixture().quote,
      revision_status: "sent",
      board_stage: "sent_follow_up",
      version: 4,
    },
  });
}

describe("CloseQuoteDialog", () => {
  beforeEach(() => {
    vi.mocked(quoteClient.closeCustomerQuote).mockReset();
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <CloseQuoteDialog open={false} item={null} onClose={vi.fn()} onClosed={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("requires selecting an outcome before enabling Cerrar cotización", () => {
    render(<CloseQuoteDialog open item={sentItem()} onClose={vi.fn()} onClosed={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Cerrar cotización" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /^Ganada/ }));
    expect(screen.getByRole("button", { name: "Cerrar cotización" })).toBeEnabled();
  });

  it("submits outcome=won with the quote's current version and an idempotency key", async () => {
    const item = sentItem();
    const onClosed = vi.fn();
    vi.mocked(quoteClient.closeCustomerQuote).mockResolvedValue({
      ...item.quote,
      revision_status: "closed_won",
      board_stage: "closed",
      quote_outcome: "won",
    });

    render(<CloseQuoteDialog open item={item} onClose={vi.fn()} onClosed={onClosed} />);
    fireEvent.click(screen.getByRole("radio", { name: /^Ganada/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cerrar cotización" }));

    await waitFor(() =>
      expect(quoteClient.closeCustomerQuote).toHaveBeenCalledWith(
        item.quote.quote_id,
        { expected_version: item.quote.version, outcome: "won" },
        expect.any(String),
      ),
    );
    await waitFor(() => expect(onClosed).toHaveBeenCalled());
  });

  it("submits outcome=null when Nula is selected", async () => {
    const item = sentItem();
    vi.mocked(quoteClient.closeCustomerQuote).mockResolvedValue({
      ...item.quote,
      revision_status: "closed_null",
      board_stage: "closed",
      quote_outcome: "null",
    });

    render(<CloseQuoteDialog open item={item} onClose={vi.fn()} onClosed={vi.fn()} />);
    fireEvent.click(screen.getByRole("radio", { name: /^Nula/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cerrar cotización" }));

    await waitFor(() =>
      expect(quoteClient.closeCustomerQuote).toHaveBeenCalledWith(
        item.quote.quote_id,
        { expected_version: item.quote.version, outcome: "null" },
        expect.any(String),
      ),
    );
  });

  it("Cancelar does not submit and resets the outcome selection", () => {
    const onClose = vi.fn();
    render(<CloseQuoteDialog open item={sentItem()} onClose={onClose} onClosed={vi.fn()} />);

    fireEvent.click(screen.getByRole("radio", { name: /^Ganada/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(onClose).toHaveBeenCalled();
    expect(quoteClient.closeCustomerQuote).not.toHaveBeenCalled();
  });

  it("shows a specific error message from a known reason code and does not call onClosed", async () => {
    const item = sentItem();
    const onClosed = vi.fn();
    vi.mocked(quoteClient.closeCustomerQuote).mockRejectedValue(
      new OperatorApiError(
        JSON.stringify({ detail: "customer_quote_illegal_transition: cannot close from 'approved'" }),
        409,
      ),
    );

    render(<CloseQuoteDialog open item={item} onClose={vi.fn()} onClosed={onClosed} />);
    fireEvent.click(screen.getByRole("radio", { name: /^Ganada/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cerrar cotización" }));

    await waitFor(() => screen.getByRole("alert"));
    expect(screen.getByRole("alert")).toHaveTextContent(/estado actual/i);
    expect(onClosed).not.toHaveBeenCalled();
  });

  it("never renders when item is null even if open is true", () => {
    const { container } = render(
      <CloseQuoteDialog open item={null} onClose={vi.fn()} onClosed={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
