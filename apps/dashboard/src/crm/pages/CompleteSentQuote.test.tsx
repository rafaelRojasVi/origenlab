import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: { body: Record<string, unknown> }[] = [];
vi.mock("../../api/v2Client", () => ({
  fetchV2Organizations: vi.fn(async () => ({ items: [{ organization_id: "org-1", name: "Instituto Ficticio (IF)" }] })),
}));
vi.mock("../authoring/crmAuthoringApi", () => ({
  fetchOrganizationAuthoring: vi.fn(async () => ({ organization: { id: "org-1", name: "Instituto Ficticio (IF)", version: 4 } })),
}));
vi.mock("../caseCommands", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  completeCaseFromSentQuote: vi.fn(async (body: Record<string, unknown>) => {
    calls.push({ body });
    return { opportunity_id: "caso-1", opportunity_version: 9, quote_number: "09876-26" };
  }),
}));

import { CompleteSentQuote } from "./CompleteSentQuote";

beforeEach(() => { calls.length = 0; });

describe("Completar con la cotización del correo", () => {
  it("creates the institution the PDF names when nobody picks an existing one", async () => {
    const done = vi.fn();
    render(<CompleteSentQuote sourceRecordId="s-1" quoteNumber="09876-26" suggestion="Instituto Nuevo (IN)" onDone={done} />);
    expect(screen.getByLabelText("Institución")).toHaveValue("Instituto Nuevo (IN)");
    fireEvent.click(screen.getByRole("button", { name: /Completar/ }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("caso-1"));
    expect(calls[0].body).toMatchObject({ source_record_id: "s-1", new_organization_name: "Instituto Nuevo (IN)" });
    expect(calls[0].body).not.toHaveProperty("organization_id");
  });

  it("uses an existing institution on an open case, with both versions", async () => {
    const done = vi.fn();
    render(<CompleteSentQuote sourceRecordId="s-1" quoteNumber="09876-26" suggestion="Instituto"
      opportunity={{ id: "caso-1", version: 3 }} onDone={done} />);
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    fireEvent.click(await screen.findByRole("radio", { name: "Instituto Ficticio (IF)" }));
    fireEvent.click(screen.getByRole("button", { name: /Completar/ }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(calls[0].body).toMatchObject({ opportunity_id: "caso-1", opportunity_version: 3,
      organization_id: "org-1", organization_version: 4 });
    expect(calls[0].body).not.toHaveProperty("new_organization_name");
  });

  it("asks for an institution when the PDF names only a person", () => {
    render(<CompleteSentQuote sourceRecordId="s-1" quoteNumber="09876-26" suggestion={null} onDone={vi.fn()} />);
    expect(screen.getByLabelText("Institución")).toHaveValue("");
    expect(screen.getByRole("button", { name: /Completar/ })).toBeDisabled();
  });
});
