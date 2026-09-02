import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuoteWorkflowActions } from "./QuoteWorkflowActions";

describe("QuoteWorkflowActions", () => {
  it("renders one button for draft: Enviar a aprobación", () => {
    render(
      <QuoteWorkflowActions
        revisionStatus="draft"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Enviar a aprobación" })).toBeInTheDocument();
  });

  it("renders one button for adjustments_requested: Enviar a aprobación", () => {
    render(
      <QuoteWorkflowActions
        revisionStatus="adjustments_requested"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Enviar a aprobación" })).toBeInTheDocument();
  });

  it("renders two buttons for pending_approval: Aprobar and Solicitar ajustes", () => {
    render(
      <QuoteWorkflowActions
        revisionStatus="pending_approval"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Solicitar ajustes" })).toBeInTheDocument();
  });

  it("renders one button for approved: Confirmar envío", () => {
    render(
      <QuoteWorkflowActions
        revisionStatus="approved"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Confirmar envío" })).toBeInTheDocument();
  });

  it("renders no actions for sent (terminal for these four commands)", () => {
    const { container } = render(
      <QuoteWorkflowActions
        revisionStatus="sent"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("clicking a non-confirmation action calls onDispatch with the command", () => {
    const onDispatch = vi.fn();
    render(
      <QuoteWorkflowActions
        revisionStatus="draft"
        disabled={false}
        onDispatch={onDispatch}
        onRequestConfirmation={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Enviar a aprobación" }));
    expect(onDispatch).toHaveBeenCalledWith("submit_for_review");
  });

  it("clicking a confirmation-required action calls onRequestConfirmation, not onDispatch", () => {
    const onDispatch = vi.fn();
    const onRequestConfirmation = vi.fn();
    render(
      <QuoteWorkflowActions
        revisionStatus="pending_approval"
        disabled={false}
        onDispatch={onDispatch}
        onRequestConfirmation={onRequestConfirmation}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Solicitar ajustes" }));
    expect(onRequestConfirmation).toHaveBeenCalledWith("request_adjustments");
    expect(onDispatch).not.toHaveBeenCalled();
  });

  it("clicking approved's confirm_send action calls onRequestConfirmation", () => {
    const onRequestConfirmation = vi.fn();
    render(
      <QuoteWorkflowActions
        revisionStatus="approved"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={onRequestConfirmation}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirmar envío" }));
    expect(onRequestConfirmation).toHaveBeenCalledWith("confirm_send");
  });

  it("disables every action button when disabled is true", () => {
    render(
      <QuoteWorkflowActions
        revisionStatus="pending_approval"
        disabled={true}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    for (const button of screen.getAllByRole("button")) {
      expect(button).toBeDisabled();
    }
  });
});
