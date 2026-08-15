import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { QueueName } from "../../api/institutionIntel/types";
import { MOCK_QUEUE_ROWS } from "../../api/institutionIntel/mockData";
import { ProspectQueueList } from "./ProspectQueueList";

vi.mock("../../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    listQueueRows: vi.fn(
      async ({ queue, page = 1, pageSize = 15 }: { queue: QueueName; page?: number; pageSize?: number }) => {
        const all = MOCK_QUEUE_ROWS.filter((row) => row.queue === queue);
        const start = (page - 1) * pageSize;
        const items = all.slice(start, start + pageSize);
        return { items, pageInfo: { page, pageSize, totalItems: all.length } };
      },
    ),
  },
}));

describe("ProspectQueueList", () => {
  it("renders rows from multiple queues and lets a row open its institution", async () => {
    const onOpenInstitution = vi.fn();
    render(<ProspectQueueList onOpenInstitution={onOpenInstitution} />);

    // Same institution appears in two different queue rows (current opportunity + contact gap),
    // so wait for that directly rather than a singular getByText, which would throw on multiples.
    await waitFor(() => {
      expect(screen.getAllByText("Hospital Regional de Talca").length).toBeGreaterThanOrEqual(2);
    });
    screen.getByText("Universidad de Concepción");
    screen.getByText(/mantención de centrífugas/);

    fireEvent.click(screen.getAllByText("Hospital Regional de Talca")[0]);
    expect(onOpenInstitution).toHaveBeenCalledWith("inst-talca-01");
  });

  it("filters to a single queue via the filter chips", async () => {
    render(<ProspectQueueList onOpenInstitution={() => {}} />);
    await waitFor(() => {
      screen.getByText("Universidad de Concepción");
    });

    fireEvent.click(screen.getByRole("button", { name: "Prospecto histórico" }));

    // Wait for the new filtered state to actually settle (not just for the old
    // row to vanish — it also vanishes transiently during the loading skeleton).
    await waitFor(() => {
      screen.getByText("Servicio de Salud Ñuble");
    });
    expect(screen.queryByText("Universidad de Concepción")).toBeNull();
  });

  it("states the queue-merge is a frontend decision, not backend-provided", async () => {
    render(<ProspectQueueList onOpenInstitution={() => {}} />);
    await waitFor(() => {
      screen.getByText(/sintetiza las seis colas del backend en una sola lista/);
    });
  });
});
