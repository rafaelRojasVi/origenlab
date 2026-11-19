import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState, ProfileCard } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData } from "../crmTypes";
import { useState } from "react";
import { applyCaseReceipt, type CaseCommandReceipt } from "../caseCommands";
import { FollowUpPicker } from "./FollowUpPicker";

// Every value below is invented; the repository is public.
const PROFILES: ProfileCard[] = [
  { id: "op-rafa", displayName: "Rafa", roleLabel: "Ventas", key: "rafael" },
  { id: "op-tati", displayName: "Tati", roleLabel: "Ventas", key: "tati" },
  { id: "op-karla", displayName: "Karla", roleLabel: "Ventas", key: "karla" },
];

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  return {
    opportunity_id: "11111111-1111-4111-8111-111111111111",
    title: "Cotización CN09876",
    stage: "quoting",
    version: 4,
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: { organization_id: "org-1", name: "Instituto Ficticio", confirmation: "confirmed", version: 2 },
    other_organizations: [],
    contact: { phone: "+56 9 1234 5678" } as OpportunityCardData["contact"],
    quotes: [],
    quote_numbers: [],
    revision_count: 0,
    latest_revision: null,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
    follow_up: { operator_id: null, operator_name: null, channel: null },
    ...over,
  };
}

interface Post {
  body: Record<string, unknown>;
  key: string | null;
}

function stubPosts(): Post[] {
  const posts: Post[] = [];
  let version = 4;
  vi.stubGlobal(
    "fetch",
    vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      const headers = init?.headers as Record<string, string>;
      posts.push({ body, key: headers["Idempotency-Key"] ?? null });
      version += 1;
      return Promise.resolve(
        new Response(
          JSON.stringify({
            command: "set_case_follow_up",
            opportunity_id: body.opportunity_id,
            opportunity_version: version,
            operator_id: body.follow_up_operator_id,
            channel: body.channel,
            command_receipt_id: `r-${version}`,
            idempotency_key: "k",
            replayed: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }),
  );
  return posts;
}

function renderPicker(c: OpportunityCardData, role = "sales", onChanged = vi.fn()) {
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <FollowUpPicker card={c} profiles={PROFILES} onChanged={onChanged} />
    </AuthSessionContext.Provider>,
  );
  return onChanged;
}

const owner = () => screen.getByLabelText("Encargado") as HTMLSelectElement;
const channel = () => screen.getByLabelText("Próximo contacto") as HTMLSelectElement;
const optionTexts = (el: HTMLSelectElement) => Array.from(el.options).map((o) => o.value);

afterEach(() => vi.unstubAllGlobals());

describe("FollowUpPicker", () => {
  it("renders suggestion when unset", () => {
    renderPicker(card());
    // A phone and no sonicator or big amount: Rafa is suggested and shown as selected.
    expect(owner().value).toBe("op-rafa");
    expect(screen.getByText("Rafa es el sugerido.")).toBeInTheDocument();
    expect(owner().selectedOptions[0]).toHaveTextContent("Rafa (sugerido)");
    expect(optionTexts(owner())).toContain("");
  });

  it("whatsapp option only for Tati", () => {
    renderPicker(card({ follow_up: { operator_id: "op-karla", operator_name: "Karla", channel: "llamada" } }));
    expect(optionTexts(channel())).toEqual(["llamada", "correo"]);
    stubPosts();
    fireEvent.change(owner(), { target: { value: "op-tati" } });
    return waitFor(() => expect(optionTexts(channel())).toEqual(["llamada", "correo", "whatsapp"]));
  });

  it("changing owner from Tati resets whatsapp in one command", async () => {
    const posts = stubPosts();
    const onChanged = renderPicker(card({ follow_up: { operator_id: "op-tati", operator_name: "Tati", channel: "whatsapp" } }));
    fireEvent.change(owner(), { target: { value: "op-karla" } });
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({
      opportunity_id: "11111111-1111-4111-8111-111111111111",
      opportunity_version: 4,
      follow_up_operator_id: "op-karla",
      channel: "llamada",
    });
    expect(posts[0].key).toBeTruthy();
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ opportunity_version: 5 })));
  });

  it("never sends whatsapp with no encargado", async () => {
    const posts = stubPosts();
    renderPicker(card({ follow_up: { operator_id: "op-tati", operator_name: "Tati", channel: "whatsapp" } }));
    fireEvent.change(owner(), { target: { value: "" } });
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ follow_up_operator_id: null, channel: "llamada" });
  });

  it("confirming the suggested person sends the command", async () => {
    const posts = stubPosts();
    renderPicker(card());
    fireEvent.click(screen.getByRole("button", { name: /Confirmar/ }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ follow_up_operator_id: "op-rafa", channel: "llamada" });
  });

  it("two quick changes use the returned version", async () => {
    const posts = stubPosts();
    renderPicker(card({ follow_up: { operator_id: "op-karla", operator_name: "Karla", channel: "llamada" } }));
    fireEvent.change(owner(), { target: { value: "op-rafa" } });
    await waitFor(() => expect(owner().disabled).toBe(false));
    fireEvent.change(channel(), { target: { value: "correo" } });
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[0].body.opportunity_version).toBe(4);
    expect(posts[1].body.opportunity_version).toBe(5);
    expect(posts[1].body).toMatchObject({ follow_up_operator_id: "op-rafa", channel: "correo" });
    expect(posts[1].key).not.toBe(posts[0].key);
  });

  it("hidden for viewer role", () => {
    renderPicker(card(), "viewer");
    expect(screen.queryByLabelText("Encargado")).not.toBeInTheDocument();
  });

  it("only renders one of the two selects, with the other kept in the command", async () => {
    const posts = stubPosts();
    const c = card({ follow_up: { operator_id: "op-tati", operator_name: "Tati", channel: "whatsapp" } });
    const { unmount } = render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <FollowUpPicker card={c} profiles={PROFILES} onChanged={vi.fn()} compact only="owner" />
      </AuthSessionContext.Provider>,
    );
    expect(screen.queryByLabelText("Próximo contacto")).not.toBeInTheDocument();
    fireEvent.change(owner(), { target: { value: "op-karla" } });
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ follow_up_operator_id: "op-karla", channel: "llamada" });
    unmount();
    render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <FollowUpPicker card={c} profiles={PROFILES} onChanged={vi.fn()} compact only="channel" />
      </AuthSessionContext.Provider>,
    );
    expect(screen.queryByLabelText("Encargado")).not.toBeInTheDocument();
    expect(channel().value).toBe("whatsapp");
  });

  it("owner then channel on two pickers of one row use the version the first receipt returned", async () => {
    const posts = stubPosts();
    function Row() {
      const [c, setC] = useState(card({ follow_up: { operator_id: "op-karla", operator_name: "Karla", channel: "llamada" } }));
      const apply = (r: CaseCommandReceipt) => setC((prev) => applyCaseReceipt([prev], r)[0]);
      return (
        <>
          <FollowUpPicker card={c} profiles={PROFILES} onChanged={apply} compact only="owner" />
          <FollowUpPicker card={c} profiles={PROFILES} onChanged={apply} compact only="channel" />
        </>
      );
    }
    render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <Row />
      </AuthSessionContext.Provider>,
    );
    fireEvent.change(owner(), { target: { value: "op-tati" } });
    await waitFor(() => expect(optionTexts(channel())).toContain("whatsapp"));
    fireEvent.change(channel(), { target: { value: "whatsapp" } });
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1].body).toMatchObject({ opportunity_version: 5, follow_up_operator_id: "op-tati", channel: "whatsapp" });
  });

  it("a 409 asks the page to refetch", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 409 }))));
    const onConflict = vi.fn();
    render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <FollowUpPicker card={card()} profiles={PROFILES} onChanged={vi.fn()} onConflict={onConflict} />
      </AuthSessionContext.Provider>,
    );
    fireEvent.change(owner(), { target: { value: "op-karla" } });
    await waitFor(() => expect(onConflict).toHaveBeenCalledTimes(1));
  });
});
