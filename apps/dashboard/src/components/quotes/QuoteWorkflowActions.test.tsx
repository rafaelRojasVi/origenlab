import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuoteWorkflowActions } from "./QuoteWorkflowActions";

describe("QuoteWorkflowActions", () => {
  it("renders one button for preparation: Enviar a revisión", () => {
    render(
      <QuoteWorkflowActions
        boardStage="preparation"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Enviar a revisión" })).toBeInTheDocument();
  });

  it("renders two buttons for review: Aprobar and Solicitar ajustes", () => {
    render(
      <QuoteWorkflowActions
        boardStage="review"
        disabled={false}
        onDispatch={vi.fn()}
        onRequestConfirmation={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Solicitar ajustes" })).toBeInTheDocument();
  });

  it("renders no actions for sent_follow_up (terminal)", () => {
    const { container } = render(
      <QuoteWorkflowActions
        boardStage="sent_follow_up"
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
        boardStage="preparation"
        disabled={false}
        onDispatch={onDispatch}
        onRequestConfirmation={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Enviar a revisión" }));
    expect(onDispatch).toHaveBeenCalledWith("submit_for_review");
  });

  it("clicking a confirmation-required action calls onRequestConfirmation, not onDispatch", () => {
    const onDispatch = vi.fn();
    const onRequestConfirmation = vi.fn();
    render(
      <QuoteWorkflowActions
        boardStage="review"
        disabled={false}
        onDispatch={onDispatch}
        onRequestConfirmation={onRequestConfirmation}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Solicitar ajustes" }));
    expect(onRequestConfirmation).toHaveBeenCalledWith("request_adjustments");
    expect(onDispatch).not.toHaveBeenCalled();
  });

  it("clicking approved_to_send's confirm_send action calls onRequestConfirmation", () => {
    const onRequestConfirmation = vi.fn();
    render(
      <QuoteWorkflowActions
        boardStage="approved_to_send"
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
        boardStage="review"
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
