import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkflowConfirmDialog } from "./WorkflowConfirmDialog";
import { globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";

describe("WorkflowConfirmDialog", () => {
  it("renders nothing when item is null", () => {
    render(
      <WorkflowConfirmDialog
        open
        item={null}
        title="Solicitar ajustes"
        message="mensaje"
        confirmLabel="Solicitar ajustes"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the title and message, and never dispatches before an explicit confirm click", () => {
    const onConfirm = vi.fn();
    render(
      <WorkflowConfirmDialog
        open
        item={globalQuoteItemFixture()}
        title="Confirmar envío"
        message="Esta acción confirma que la cotización fue enviada por otro medio."
        confirmLabel="Confirmar envío"
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Confirmar envío" })).toBeInTheDocument();
    expect(screen.getByText(/enviada por otro medio/)).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("clicking the confirm button calls onConfirm with the item, then closes", async () => {
    const item = globalQuoteItemFixture();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <WorkflowConfirmDialog
        open
        item={item}
        title="Confirmar envío"
        message="mensaje"
        confirmLabel="Confirmar envío"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Confirmar envío" }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(item));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("clicking Cancelar closes without calling onConfirm", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <WorkflowConfirmDialog
        open
        item={globalQuoteItemFixture()}
        title="Solicitar ajustes"
        message="mensaje"
        confirmLabel="Solicitar ajustes"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("shows an error and stays open when onConfirm rejects", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error("boom"));
    const onClose = vi.fn();
    render(
      <WorkflowConfirmDialog
        open
        item={globalQuoteItemFixture()}
        title="Confirmar envío"
        message="mensaje"
        confirmLabel="Confirmar envío"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Confirmar envío" }));

    await waitFor(() => screen.getByRole("alert"));
    expect(onClose).not.toHaveBeenCalled();
  });
});
