import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SaveStatus, savedAgo } from "./SaveStatus";

describe("savedAgo", () => {
  it("says seconds, then minutes, then the hour", () => {
    const now = Date.parse("2026-10-16T12:00:00Z");
    expect(savedAgo(now - 4_000, now)).toBe("hace 4 s");
    expect(savedAgo(now + 500, now)).toBe("hace 0 s");
    expect(savedAgo(now - 5 * 60_000, now)).toBe("hace 5 min");
    expect(savedAgo(now - 3 * 3600_000, now)).toMatch(/^a las \d{2}:\d{2}$/);
  });
});

describe("SaveStatus", () => {
  beforeEach(() => vi.useFakeTimers({ now: Date.parse("2026-10-16T12:00:00Z") }));
  afterEach(() => vi.useRealTimers());

  it("counts the seconds since the last save", () => {
    render(<SaveStatus saveState="saved" lastSavedAt={Date.now() - 4_000} onRetry={() => undefined} />);
    expect(screen.getByText("guardado automáticamente hace 4 s")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(screen.getByText("guardado automáticamente hace 6 s")).toBeInTheDocument();
  });

  it("says «guardado» when nothing changed since it was opened", () => {
    render(<SaveStatus saveState="idle" lastSavedAt={null} onRetry={() => undefined} />);
    expect(screen.getByText("guardado")).toBeInTheDocument();
  });

  it("says when it is saving and when edits are waiting", () => {
    const { rerender } = render(<SaveStatus saveState="saving" lastSavedAt={null} onRetry={() => undefined} />);
    expect(screen.getByText("guardando…")).toBeInTheDocument();
    rerender(<SaveStatus saveState="dirty" lastSavedAt={null} onRetry={() => undefined} />);
    expect(screen.getByText("sin guardar")).toBeInTheDocument();
  });

  it("offers a retry when the save failed", () => {
    const onRetry = vi.fn();
    render(<SaveStatus saveState="error" lastSavedAt={null} onRetry={onRetry} />);
    expect(screen.getByText(/no se pudo guardar/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("says nothing for a conflict (the page shows its banner)", () => {
    const { container } = render(<SaveStatus saveState="conflict" lastSavedAt={null} onRetry={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says why the save was refused, and still offers the retry", () => {
    const onRetry = vi.fn();
    render(<SaveStatus saveState="error" lastSavedAt={null} message="Falta configurar la numeración." onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("no se pudo guardar: Falta configurar la numeración. · Reintentar");
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(onRetry).toHaveBeenCalled();
  });
});
