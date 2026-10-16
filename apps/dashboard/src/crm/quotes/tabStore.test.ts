/** The Cotizador module's tabs. Synthetic ids and names only: this repository is public. */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { workspaceFixture } from "./quoteFixtures";
import { forgetTabEditors, registerTabEditor } from "./tabEditors";
import {
  EMPTY_TABS,
  MAX_TABS,
  activateTab,
  closeTab,
  initialTabs,
  loadTabs,
  openTab,
  saveTabs,
  setTabMeta,
  tabMetaOf,
  tabStatus,
  tabsStorageKey,
  useQuoteTabs,
  type TabMeta,
  type TabsState,
} from "./tabStore";

const id = (n: number) => `6bafddfa-0000-4000-8000-${String(n).padStart(12, "0")}`;
const meta = (n: number, status: TabMeta["status"] = "draft"): TabMeta => ({ label: `CN${String(n).padStart(5, "0")}`, client: `Cliente ${n}`, status });
const OP = "op-ventas";

afterEach(() => {
  localStorage.clear();
  forgetTabEditors();
});

describe("tab store — pure", () => {
  it("opens a tab at the end and activates it; the same quote is never open twice", () => {
    let s = openTab(EMPTY_TABS, id(1), meta(1));
    s = openTab(s, id(2), meta(2));
    expect(s.tabs.map((t) => t.id)).toEqual([id(1), id(2)]);
    expect(s.active).toBe(id(2));
    const again = openTab(s, id(1));
    expect(again.tabs.map((t) => t.id)).toEqual([id(1), id(2)]);
    expect(again.active).toBe(id(1));
    expect(again.tabs[0].meta).toEqual(meta(1));
  });

  it("closing the active tab activates its right neighbour, else its left one, else «Todas»", () => {
    let s: TabsState = [1, 2, 3].reduce((acc, n) => openTab(acc, id(n), meta(n)), EMPTY_TABS);
    s = activateTab(s, id(2));
    s = closeTab(s, id(2));
    expect(s.active).toBe(id(3));
    s = closeTab(s, id(3));
    expect(s.active).toBe(id(1));
    s = closeTab(s, id(1));
    expect(s).toEqual({ tabs: [], active: null });
  });

  it("closing another tab keeps the active one", () => {
    let s: TabsState = [1, 2].reduce((acc, n) => openTab(acc, id(n), meta(n)), EMPTY_TABS);
    s = closeTab(s, id(1));
    expect(s.active).toBe(id(2));
  });

  it("activates «Todas» with null, and ignores a quote that is not open", () => {
    const s = openTab(EMPTY_TABS, id(1), meta(1));
    expect(activateTab(s, null).active).toBeNull();
    expect(activateTab(s, id(9))).toBe(s);
  });

  it("updates a tab's meta, and returns the same state when nothing changed", () => {
    const s = openTab(EMPTY_TABS, id(1));
    const next = setTabMeta(s, id(1), meta(1, "approved"));
    expect(next.tabs[0].meta).toEqual(meta(1, "approved"));
    expect(setTabMeta(next, id(1), meta(1, "approved"))).toBe(next);
    expect(setTabMeta(next, id(9), meta(9))).toBe(next);
  });

  it("maps a revision status to the tab's dot", () => {
    expect(tabStatus("draft")).toBe("draft");
    expect(tabStatus("in_review")).toBe("draft");
    expect(tabStatus("approved")).toBe("approved");
    expect(tabStatus("sent")).toBe("sent");
    expect(tabStatus("void")).toBe("void");
    expect(tabStatus(undefined)).toBe("draft");
  });
});

describe("tab meta from a workspace", () => {
  it("names the revision shown, the institution and the state", () => {
    expect(tabMetaOf(workspaceFixture())).toEqual({ label: "CN00042", client: "Institución de prueba", status: "draft" });
    expect(tabMetaOf(workspaceFixture({ status: "approved", revision_no: 2, label: "00042A-26" }))).toEqual({
      label: "CN00042A",
      client: "Institución de prueba",
      status: "approved",
    });
  });

  it("falls back to the chosen recipient, then to the case's title", () => {
    const ws = workspaceFixture({ recipient_participant_id: "pa-1" });
    const noInstitution = { ...ws, case: { ...ws.case, institution: null, participants: [{ id: "pa-1", name: "Persona Ejemplo", email: null, primary: true, role: "requester" }] } };
    expect(tabMetaOf(noInstitution).client).toBe("Persona Ejemplo");
    expect(tabMetaOf({ ...noInstitution, case: { ...noInstitution.case, participants: [] } }).client).toBe("Caso de prueba");
  });
});

describe("tab store — persistence", () => {
  it("keeps the tabs per operator, under cotizador:tabs:<operator_id>", () => {
    const s = openTab(openTab(EMPTY_TABS, id(1), meta(1)), id(2), meta(2, "sent"));
    saveTabs(OP, s);
    expect(tabsStorageKey(OP)).toBe("cotizador:tabs:op-ventas");
    expect(localStorage.getItem("cotizador:tabs:op-ventas")).not.toBeNull();
    expect(loadTabs(OP)).toEqual(s);
    expect(loadTabs("op-otro")).toEqual(EMPTY_TABS);
  });

  it("restores nothing it cannot trust: a broken value, a bad id, more than eight tabs, an unknown active tab", () => {
    localStorage.setItem(tabsStorageKey(OP), "{not json");
    expect(loadTabs(OP)).toEqual(EMPTY_TABS);
    const many = Array.from({ length: 10 }, (_, i) => ({ id: id(i + 1), meta: meta(i + 1) }));
    localStorage.setItem(
      tabsStorageKey(OP),
      JSON.stringify({ v: 1, tabs: [{ id: "../x", meta: meta(0) }, { id: id(1), meta: { label: 5 } }, ...many.slice(1)], active: id(42) }),
    );
    const s = loadTabs(OP);
    expect(s.tabs).toHaveLength(MAX_TABS);
    expect(s.tabs[0]).toEqual({ id: id(1), meta: null });
    expect(s.active).toBeNull();
  });
});

describe("the state a page opens with", () => {
  it("shows the address's quote: already open, or added when there is room", () => {
    saveTabs(OP, openTab(openTab(EMPTY_TABS, id(1), meta(1)), id(2), meta(2)));
    expect(initialTabs(OP, id(1)).active).toBe(id(1));
    const added = initialTabs(OP, id(3));
    expect(added.tabs.map((t) => t.id)).toEqual([id(1), id(2), id(3)]);
    expect(added.active).toBe(id(3));
    expect(loadTabs(OP)).toEqual(added);
  });

  it("keeps the stored active tab without an address, and shows «Todas» while eight are open and the quote is not", () => {
    const full: TabsState = Array.from({ length: MAX_TABS }, (_, i) => i + 1).reduce((acc, n) => openTab(acc, id(n), meta(n)), EMPTY_TABS);
    saveTabs(OP, full);
    expect(initialTabs(OP, null).active).toBe(id(MAX_TABS));
    const s = initialTabs(OP, id(9));
    expect(s.active).toBeNull();
    expect(s.tabs).toHaveLength(MAX_TABS);
  });
});

describe("useQuoteTabs", () => {
  function setup(ask: { discard?: () => Promise<boolean>; evict?: () => Promise<boolean> } = {}) {
    const discard = vi.fn(ask.discard ?? (() => Promise.resolve(false)));
    const evict = vi.fn(ask.evict ?? (() => Promise.resolve(false)));
    const hook = renderHook(() => useQuoteTabs(OP, { discard, evict }));
    return { hook, discard, evict };
  }

  it("persists what it opens, and a new hook restores it", async () => {
    const { hook } = setup();
    await act(async () => {
      await hook.result.current.open(id(1), meta(1));
      await hook.result.current.open(id(2), meta(2));
    });
    hook.unmount();
    const again = renderHook(() => useQuoteTabs(OP, { discard: vi.fn(), evict: vi.fn() }));
    expect(again.result.current.state.tabs.map((t) => t.id)).toEqual([id(1), id(2)]);
    expect(again.result.current.state.active).toBe(id(2));
  });

  it("closes a tab with nothing pending at once, without flushing", async () => {
    const flush = vi.fn(() => Promise.resolve(true));
    registerTabEditor(id(1), { hasPendingSave: () => false, flush });
    const { hook } = setup();
    await act(async () => void (await hook.result.current.open(id(1), meta(1))));
    await act(async () => expect(await hook.result.current.close(id(1))).toBe(true));
    expect(flush).not.toHaveBeenCalled();
    expect(hook.result.current.state.tabs).toEqual([]);
  });

  it("flushes a pending save before closing", async () => {
    const flush = vi.fn(() => Promise.resolve(true));
    registerTabEditor(id(1), { hasPendingSave: () => true, flush });
    const { hook, discard } = setup();
    await act(async () => void (await hook.result.current.open(id(1), meta(1))));
    await act(async () => expect(await hook.result.current.close(id(1))).toBe(true));
    expect(flush).toHaveBeenCalledTimes(1);
    expect(discard).not.toHaveBeenCalled();
    expect(hook.result.current.state.tabs).toEqual([]);
  });

  it("asks before closing when the flush fails, and keeps the tab when told not to", async () => {
    registerTabEditor(id(1), { hasPendingSave: () => true, flush: () => Promise.resolve(false) });
    const { hook, discard } = setup();
    await act(async () => void (await hook.result.current.open(id(1), meta(1))));
    await act(async () => expect(await hook.result.current.close(id(1))).toBe(false));
    expect(discard).toHaveBeenCalledWith(expect.objectContaining({ id: id(1) }));
    expect(hook.result.current.state.tabs.map((t) => t.id)).toEqual([id(1)]);
    discard.mockResolvedValue(true);
    await act(async () => expect(await hook.result.current.close(id(1))).toBe(true));
    expect(hook.result.current.state.tabs).toEqual([]);
  });

  it("treats a flush that throws as a failed one", async () => {
    registerTabEditor(id(1), { hasPendingSave: () => true, flush: () => Promise.reject(new Error("red")) });
    const { hook, discard } = setup();
    await act(async () => void (await hook.result.current.open(id(1), meta(1))));
    await act(async () => expect(await hook.result.current.close(id(1))).toBe(false));
    expect(discard).toHaveBeenCalledTimes(1);
  });

  it(`opens at most ${MAX_TABS}: the ninth closes the oldest tab with nothing pending`, async () => {
    // The oldest tab is pending; the second-oldest is the one to go.
    registerTabEditor(id(1), { hasPendingSave: () => true, flush: () => Promise.resolve(true) });
    const { hook, evict } = setup();
    await act(async () => {
      for (let n = 1; n <= MAX_TABS; n++) await hook.result.current.open(id(n), meta(n));
    });
    expect(hook.result.current.state.tabs).toHaveLength(MAX_TABS);
    await act(async () => expect(await hook.result.current.open(id(9), meta(9))).toBe(true));
    const ids = hook.result.current.state.tabs.map((t) => t.id);
    expect(ids).toHaveLength(MAX_TABS);
    expect(ids).not.toContain(id(2));
    expect(ids[0]).toBe(id(1));
    expect(ids[MAX_TABS - 1]).toBe(id(9));
    expect(hook.result.current.state.active).toBe(id(9));
    expect(evict).not.toHaveBeenCalled();
  });

  it("asks before the ninth when every tab has a pending save, and opens nothing when told not to", async () => {
    const flushes: string[] = [];
    for (let n = 1; n <= MAX_TABS; n++) {
      registerTabEditor(id(n), { hasPendingSave: () => true, flush: () => (flushes.push(id(n)), Promise.resolve(true)) });
    }
    const { hook, evict } = setup();
    await act(async () => {
      for (let n = 1; n <= MAX_TABS; n++) await hook.result.current.open(id(n), meta(n));
    });
    await act(async () => expect(await hook.result.current.open(id(9), meta(9))).toBe(false));
    expect(evict).toHaveBeenCalledWith(expect.objectContaining({ id: id(1) }));
    expect(hook.result.current.state.tabs.map((t) => t.id)).not.toContain(id(9));
    expect(flushes).toEqual([]);
    // Told yes: the oldest is saved first, then closed, and the ninth opens.
    evict.mockResolvedValue(true);
    await act(async () => expect(await hook.result.current.open(id(9), meta(9))).toBe(true));
    expect(flushes).toEqual([id(1)]);
    const ids = hook.result.current.state.tabs.map((t) => t.id);
    expect(ids).not.toContain(id(1));
    expect(ids[MAX_TABS - 1]).toBe(id(9));
  });

  it("an already-open quote is only activated, even at the limit", async () => {
    const { hook, evict } = setup();
    await act(async () => {
      for (let n = 1; n <= MAX_TABS; n++) await hook.result.current.open(id(n), meta(n));
    });
    await act(async () => expect(await hook.result.current.open(id(3))).toBe(true));
    expect(hook.result.current.state.tabs).toHaveLength(MAX_TABS);
    expect(hook.result.current.state.active).toBe(id(3));
    expect(evict).not.toHaveBeenCalled();
  });
});
