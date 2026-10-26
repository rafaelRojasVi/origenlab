/**
 * The card after «Contactar»: no decisions left, but «Preparar correo» stays (the institution was
 * chosen first, the intro email comes after). Two matching institutions are named, not called a
 * duplicate. Invented data only.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProspectCard } from "./ProspectCard";
import type { ProspectItem } from "./prospectApi";

const NOW = new Date("2026-10-23T15:00:00Z");
const item = (over: Record<string, unknown> = {}) => ({
  id: "11111111-1111-4111-8111-111111111111", person_name: "Ana Prueba", role: "Investigadora",
  organization_name: "Instituto Ficticio", unit: null, source: "fondequip", fund_label: "Fondequip 2025",
  amount_label: null, awarded_on: "2025-10-01", why_now: "Ganó un Fondequip", equipment: [],
  email: "ana@example.test", email_source_url: null, email_source_year: null, phone: null,
  links: { website: null, linkedin_person: null, linkedin_org: null, orcid: null, scholar: null },
  has_logo: false, confidence: "alta", score: 90, state: "new", reasons: [], organization_candidates: [],
  latest_verdict: null, returns_today: false, acquired_at: "2026-10-23T09:00:00Z", ...over,
});
const noop = () => {};

function card(over: Record<string, unknown>, props: { canDecide: boolean; onPrepareEmail?: () => void }) {
  return render(
    <ProspectCard item={item(over) as ProspectItem} now={NOW} canDecide={props.canDecide} selected={false} busy={false}
      onToggle={noop} onOpen={noop} onContact={noop} onLater={noop} onDiscard={noop} onEdit={noop}
      onPrepareEmail={props.onPrepareEmail} />,
  );
}

describe("ProspectCard", () => {
  it("keeps «Preparar correo» on a contacted card with a correo", () => {
    const onPrepareEmail = vi.fn();
    card({ latest_verdict: { verdict: "contactar", reason: null, remind_on: null, reviewed_at: "2026-10-23T10:00:00Z" } },
      { canDecide: false, onPrepareEmail });
    expect(screen.queryByRole("button", { name: "Contactar a Ana Prueba" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Preparar correo para Ana Prueba" }));
    expect(onPrepareEmail).toHaveBeenCalledOnce();
  });

  it("shows nothing to do on a contacted card without a correo", () => {
    card({ email: null }, { canDecide: false, onPrepareEmail: noop });
    expect(screen.queryByRole("button", { name: /Preparar correo/ })).not.toBeInTheDocument();
  });

  it("names both institutions instead of «Posible duplicado»", () => {
    card({ organization_candidates: [{ id: "a", name: "Universidad Ficticia del Norte" }, { id: "b", name: "Universidad Ficticia del Sur" }] },
      { canDecide: true });
    const badge = screen.getByText("Dos instituciones posibles");
    expect(badge.closest("[title]")).toHaveAttribute("title", "Universidad Ficticia del Norte / Universidad Ficticia del Sur");
    expect(screen.queryByText("Posible duplicado")).not.toBeInTheDocument();
  });
});
