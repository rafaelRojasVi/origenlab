/**
 * «Preparar correo»: the drawer previews the intro email, creates a Gmail draft (never sends) and links
 * to it; a second draft is asked for with `again`. Invented data only.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmailDraftDrawer } from "./EmailDraftDrawer";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const ID = "11111111-1111-4111-8111-111111111111";
const PREVIEW = {
  to: "ana@example.test", subject: "Equipamiento para su proyecto Fondequip 2025 — OrigenLab",
  text: "Estimada Dra. Prueba:\n\nLe escribo de OrigenLab…", html: "<p>…</p>", person_name: "Ana Prueba",
  fund_label: "Fondequip 2025",
};
const DRAFTED = {
  command: "prepare-prospect-email", finding_id: ID, draft_id: "r-1", message_id: "m1",
  gmail_url: "https://mail.google.com/mail/u/0/#drafts?compose=m1", to: "ana@example.test",
  subject: PREVIEW.subject, signature: true, labels_applied: false, contacted: true, event_ids: ["e"],
  idempotency_key: "k", command_receipt_id: "r", replayed: false,
};

function stubApi() {
  const posts: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(raw, "http://localhost").pathname;
    if ((init?.method ?? "GET").toUpperCase() === "POST") {
      posts.push(JSON.parse(String(init?.body)));
      return Promise.resolve(json(DRAFTED));
    }
    if (path.endsWith("/email-preview")) return Promise.resolve(json(PREVIEW));
    return Promise.resolve(json({}, 404));
  }));
  return posts;
}

afterEach(() => vi.unstubAllGlobals());

describe("EmailDraftDrawer", () => {
  it("previews, drafts and links to the draft in Gmail", async () => {
    const posts = stubApi();
    const onDrafted = vi.fn();
    render(<EmailDraftDrawer findingId={ID} onClose={() => {}} onDrafted={onDrafted} />);
    expect(await screen.findByText(PREVIEW.subject)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Crear borrador en Gmail" }));
    const link = await screen.findByTestId("gmail-draft-link");
    expect(link).toHaveAttribute("href", DRAFTED.gmail_url);
    expect(screen.getByText(/falta autorizar Gmail de nuevo/)).toBeInTheDocument();
    expect(posts).toEqual([{ finding_id: ID, again: false }]);
    expect(onDrafted).toHaveBeenCalledOnce();
  });

  it("asks for another draft with `again` when one exists", async () => {
    const posts = stubApi();
    render(<EmailDraftDrawer findingId={ID} existingDraft={{ gmail_url: "https://mail.google.com/mail/u/0/#drafts?compose=m0" }}
      onClose={() => {}} />);
    expect(await screen.findByText(/Ya existe un borrador/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver en Gmail" })).toHaveAttribute(
      "href", "https://mail.google.com/mail/u/0/#drafts?compose=m0");
    fireEvent.click(screen.getByRole("button", { name: "Crear otro borrador" }));
    await waitFor(() => expect(posts).toEqual([{ finding_id: ID, again: true }]));
  });
});
