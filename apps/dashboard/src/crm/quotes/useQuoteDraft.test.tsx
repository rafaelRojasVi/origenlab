import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addFreeLine, removeItem, updateItem } from "./draftEditor";
import { QuoteRefusal, type Computed, type QuoteDraft, type QuoteWorkspace } from "./quoteApi";
import { computedFixture, workspaceFixture } from "./quoteFixtures";
import { PREVIEW_DELAY_MS, SAVE_DELAY_MS, useQuoteDraft, type QuoteDraftApi } from "./useQuoteDraft";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

type SaveResult = { revision_id: string; version: number; computed: Computed };
type SaveArgs = [string, number, QuoteDraft, string];

/** An API whose every call waits until the test answers it. */
function fakeApi() {
  const previews: { draft: QuoteDraft; signal?: AbortSignal; d: Deferred<{ computed: Computed }> }[] = [];
  const saves: { args: SaveArgs; d: Deferred<SaveResult> }[] = [];
  const approvals: { args: [string, number, string | undefined]; d: Deferred<{ version: number; quote_number: string; revision_label: string; negotiated_costs_skipped: unknown[] }> }[] = [];
  const reloads: Deferred<QuoteWorkspace>[] = [];
  const api: QuoteDraftApi = {
    previewQuote: vi.fn((_rev: string, draft: QuoteDraft, signal?: AbortSignal) => {
      const d = deferred<{ computed: Computed }>();
      previews.push({ draft, signal, d });
      return d.promise;
    }),
    saveQuoteDraft: vi.fn((rev: string, version: number, draft: QuoteDraft, key?: string) => {
      const d = deferred<SaveResult>();
      saves.push({ args: [rev, version, draft, key as string], d });
      return d.promise;
    }),
    approveQuoteRevision: vi.fn((rev: string, version: number, key?: string) => {
      const d = deferred<{ version: number; quote_number: string; revision_label: string; negotiated_costs_skipped: unknown[] }>();
      approvals.push({ args: [rev, version, key], d });
      return d.promise;
    }),
    fetchQuoteWorkspace: vi.fn(() => {
      const d = deferred<QuoteWorkspace>();
      reloads.push(d);
      return d.promise;
    }),
  };
  return { api, previews, saves, approvals, reloads };
}

const saved = (version: number, net = "1"): SaveResult => ({ revision_id: "r-1", version, computed: computedFixture(net) });

function fireBeforeUnload(): boolean {
  const e = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(e);
  return e.defaultPrevented;
}

/** Lets promise chains settle inside act (the timers are fake; promises are not). */
async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve();
  });
}
async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
  await settle();
}

function setup(workspace: QuoteWorkspace = workspaceFixture()) {
  const fake = fakeApi();
  const hook = renderHook(({ ws }) => useQuoteDraft(ws, { api: fake.api }), { initialProps: { ws: workspace } });
  const edit = (fn: (d: QuoteDraft) => QuoteDraft) => act(() => hook.result.current.edit(fn));
  const typeQty = (qty: string) => edit((d) => updateItem(d, 0, { quantity: qty }));
  return { ...fake, hook, edit, typeQty };
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("useQuoteDraft — live preview", () => {
  it("previews 300 ms after the last edit, once, with the latest draft", async () => {
    const { previews, typeQty } = setup();
    typeQty("2");
    await advance(PREVIEW_DELAY_MS - 50);
    typeQty("3");
    await advance(PREVIEW_DELAY_MS - 50);
    expect(previews).toHaveLength(0);
    await advance(50);
    expect(previews).toHaveLength(1);
    expect(previews[0].draft.items[0].quantity).toBe("3");
  });

  it("keeps at most one preview in flight and the last response wins (Review Focus 1)", async () => {
    const { hook, previews, typeQty } = setup();
    typeQty("2");
    await advance(PREVIEW_DELAY_MS);
    expect(previews).toHaveLength(1);
    typeQty("3");
    await advance(PREVIEW_DELAY_MS);
    expect(previews).toHaveLength(2);
    expect(previews[0].signal?.aborted).toBe(true);
    expect(previews[1].signal?.aborted).toBe(false);

    await act(async () => previews[1].d.resolve({ computed: computedFixture("333") }));
    await settle();
    // The older request answers late (its abort was ignored by the network): it must not win.
    await act(async () => previews[0].d.resolve({ computed: computedFixture("222") }));
    await settle();
    expect(hook.result.current.state.computed?.totals.net).toBe("333");
  });

  it("an aborted preview is not an error", async () => {
    const { hook, previews, typeQty } = setup();
    typeQty("2");
    await advance(PREVIEW_DELAY_MS);
    typeQty("3");
    await advance(PREVIEW_DELAY_MS);
    await act(async () => previews[0].d.reject(new DOMException("aborted", "AbortError")));
    await settle();
    expect(hook.result.current.state.previewError).toBeNull();
  });

  it("sends the draft without the legacy recipient field", async () => {
    const ws = workspaceFixture();
    ws.current!.draft = { ...ws.current!.draft!, recipient: null, recipient_participant_id: "part-1" };
    const { previews, typeQty } = setup(ws);
    typeQty("2");
    await advance(PREVIEW_DELAY_MS);
    const sent = previews[0].draft as QuoteDraft & { recipient_participant_id?: unknown };
    expect(sent.recipient).toEqual({ participant_id: "part-1" });
    expect("recipient_participant_id" in sent).toBe(false);
  });
});

describe("useQuoteDraft — autosave", () => {
  it("saves 1.2 s after the last edit with the loaded version", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS - 1);
    expect(saves).toHaveLength(0);
    await advance(1);
    expect(saves).toHaveLength(1);
    expect(saves[0].args.slice(0, 2)).toEqual(["r-1", 3]);
    expect(hook.result.current.state.saveState).toBe("saving");
    await act(async () => saves[0].d.resolve(saved(4, "444")));
    await settle();
    expect(hook.result.current.state).toMatchObject({ saveState: "saved", version: 4 });
    expect(hook.result.current.state.computed?.totals.net).toBe("444");
  });

  it("queues one follow-up while a save is in flight, using the version that save returned (Review Focus 2)", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(1);
    typeQty("3");
    await advance(SAVE_DELAY_MS);
    typeQty("4");
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(1); // still the first one in flight

    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(saves).toHaveLength(2); // exactly one follow-up
    expect(saves[1].args[1]).toBe(4);
    expect(saves[1].args[2].items[0].quantity).toBe("4"); // no edit lost
    expect(saves[1].args[3]).not.toBe(saves[0].args[3]); // a new edit, a new key

    await act(async () => saves[1].d.resolve(saved(5)));
    await settle();
    expect(saves).toHaveLength(2);
    expect(hook.result.current.state).toMatchObject({ saveState: "saved", version: 5 });
  });

  it("flush saves at once and resolves only after the queued follow-up", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    typeQty("3");
    let result: { ok: boolean; version: number } | undefined;
    act(() => {
      void hook.result.current.flush().then((r) => (result = r));
    });
    await settle();
    expect(saves).toHaveLength(1);

    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(saves).toHaveLength(2);
    expect(result).toBeUndefined();

    await act(async () => saves[1].d.resolve(saved(5)));
    await settle();
    expect(result).toEqual({ ok: true, version: 5 });
  });

  it("flush with nothing to save resolves with the current version", async () => {
    const { hook, saves } = setup();
    let result: unknown;
    await act(async () => {
      result = await hook.result.current.flush();
    });
    expect(result).toEqual({ ok: true, version: 3 });
    expect(saves).toHaveLength(0);
  });

  it("saveNow saves an unedited draft at once (B4: prices changed since the last save)", async () => {
    const { hook, saves } = setup();
    let result: { ok: boolean; version: number } | undefined;
    act(() => {
      void hook.result.current.saveNow().then((r) => (result = r));
    });
    await settle();
    expect(saves).toHaveLength(1);
    expect(saves[0].args[1]).toBe(3);
    expect(hook.result.current.state.saveState).toBe("saving");
    await act(async () => saves[0].d.resolve(saved(4, "555")));
    await settle();
    expect(result).toEqual({ ok: true, version: 4 });
    expect(hook.result.current.state.saveState).toBe("saved");
    expect(hook.result.current.state.computed?.totals.net).toBe("555");
  });

  it("saveNow with edits pending sends them once, like flush", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    act(() => void hook.result.current.saveNow());
    await settle();
    expect(saves).toHaveLength(1);
    expect(saves[0].args[2].items[0].quantity).toBe("2");
  });

  it("saveNow on an incomplete draft sends nothing and marks nothing as edited", async () => {
    const { hook, saves, previews, edit } = setup();
    edit((d) => updateItem(d, 0, { quantity: "" }));
    const seq = hook.result.current.state.editSeq;
    let result: { ok: boolean; version: number } | undefined;
    await act(async () => {
      result = await hook.result.current.saveNow();
    });
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(0);
    expect(previews).toHaveLength(0);
    expect(hook.result.current.state.editSeq).toBe(seq);
    expect(result).toEqual({ ok: false, version: 3 });
  });

  it("saveNow on an unedited incomplete draft does not make it dirty", async () => {
    const ws = workspaceFixture();
    const draft = { ...ws.current!.draft!, items: ws.current!.draft!.items.map((i, n) => (n === 0 ? { ...i, description: "" } : i)) };
    const { hook, saves } = setup(workspaceFixture({ draft }));
    expect(hook.result.current.invalid.length).toBeGreaterThan(0);
    await act(async () => {
      await hook.result.current.saveNow();
    });
    expect(saves).toHaveLength(0);
    expect(hook.result.current.state.editSeq).toBe(0);
    expect(hook.result.current.state.saveState).not.toBe("dirty");
    expect(hook.result.current.pending).toBe(false);
  });

  it("saveNow does nothing on a revision that is not an authored draft", async () => {
    const { hook, saves } = setup(workspaceFixture({ status: "approved" }));
    await act(async () => {
      await hook.result.current.saveNow();
    });
    expect(saves).toHaveLength(0);
  });

  it("retries a failed save with the same key; a new edit gets a new key", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.reject(new TypeError("network down")));
    await settle();
    expect(hook.result.current.state.saveState).toBe("error");

    act(() => void hook.result.current.flush());
    await settle();
    expect(saves).toHaveLength(2);
    expect(saves[1].args[3]).toBe(saves[0].args[3]);
    expect(saves[1].args[1]).toBe(3);
    await act(async () => saves[1].d.reject(new TypeError("network down")));
    await settle();

    typeQty("3");
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(3);
    expect(saves[2].args[3]).not.toBe(saves[0].args[3]);
  });
});

describe("useQuoteDraft — conflicts", () => {
  it.each([
    [409, "not_a_draft"],
    [404, "revision_not_found"],
  ])("a %i %s (approved or voided elsewhere) freezes the editor like a conflict", async (status, code) => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.reject(new QuoteRefusal(status, code, "only a draft revision is edited")));
    await settle();
    expect(hook.result.current.state.saveState).toBe("conflict");
    expect(hook.result.current.editable).toBe(false);
    typeQty("9");
    await advance(SAVE_DELAY_MS * 3);
    await act(async () => void (await hook.result.current.retry()));
    expect(saves).toHaveLength(1);
  });

  it("a 409 stale_version freezes the editor; reload discards local edits and resumes (B3)", async () => {
    const { hook, saves, previews, reloads, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.reject(new QuoteRefusal(409, "stale_version", "the draft was modified since you loaded it")));
    await settle();
    expect(hook.result.current.state.saveState).toBe("conflict");

    // Frozen: no edit, no preview, no save until the operator reloads.
    expect(hook.result.current.editable).toBe(false);
    const previewsBefore = previews.length;
    typeQty("9");
    await advance(SAVE_DELAY_MS * 3);
    expect(hook.result.current.state.draft.items[0].quantity).toBe("2");
    expect(previews).toHaveLength(previewsBefore);
    let flushed: unknown;
    await act(async () => {
      flushed = await hook.result.current.flush();
    });
    expect(saves).toHaveLength(1);
    expect(flushed).toEqual({ ok: false, version: 3 });

    act(() => void hook.result.current.reload());
    await settle();
    expect(reloads).toHaveLength(1);
    await act(async () => reloads[0].resolve(workspaceFixture({ version: 8 })));
    await settle();
    expect(hook.result.current.state).toMatchObject({ saveState: "idle", version: 8 });
    expect(hook.result.current.state.draft.items[0].quantity).toBe("1");
    expect(hook.result.current.editable).toBe(true);

    typeQty("5");
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(2);
    expect(saves[1].args[1]).toBe(8);
  });
});

describe("useQuoteDraft — approve", () => {
  it("saves unsaved edits first, then approves with the version that save returned (Review Focus 3)", async () => {
    const { hook, saves, approvals, reloads, typeQty } = setup();
    typeQty("2");
    let approved: unknown;
    act(() => {
      void hook.result.current.approve().then((r) => (approved = r));
    });
    await settle();
    expect(saves).toHaveLength(1); // no 1.2 s wait
    expect(approvals).toHaveLength(0);

    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(approvals).toHaveLength(1);
    expect(approvals[0].args.slice(0, 2)).toEqual(["r-1", 4]);

    await act(async () => approvals[0].d.resolve({ version: 5, quote_number: "00042-26", revision_label: "00042-26", negotiated_costs_skipped: [] }));
    await settle();
    expect(reloads).toHaveLength(1);
    await act(async () => reloads[0].resolve(workspaceFixture({ status: "approved", version: 5 })));
    await settle();
    expect(approved).toMatchObject({ version: 5 });
    expect(hook.result.current.editable).toBe(false);
  });

  it("an approve refusal reaches the caller with the API's message", async () => {
    const { hook, approvals } = setup();
    let error: unknown;
    act(() => {
      void hook.result.current.approve().catch((e) => (error = e));
    });
    await settle();
    await act(async () => approvals[0].d.reject(new QuoteRefusal(409, "placeholder_text", "Completa los textos entre corchetes")));
    await settle();
    expect(error).toBeInstanceOf(QuoteRefusal);
    expect((error as QuoteRefusal).message).toBe("Completa los textos entre corchetes");
  });

  it("saves an unedited draft whose prices changed before approving, with that save's version", async () => {
    const { hook, saves, approvals } = setup(workspaceFixture({ needs_saving: true, totals_computed_at: "2026-10-10T12:00:00Z" }));
    act(() => {
      void hook.result.current.approve().catch(() => undefined);
    });
    await settle();
    expect(saves).toHaveLength(1);
    expect(saves[0].args[1]).toBe(3);
    expect(approvals).toHaveLength(0);
    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(approvals).toHaveLength(1);
    expect(approvals[0].args.slice(0, 2)).toEqual(["r-1", 4]);
  });

  it("saves a never-priced draft before approving too", async () => {
    const { hook, saves, approvals } = setup(workspaceFixture({ needs_saving: true, totals_computed_at: null }));
    act(() => {
      void hook.result.current.approve().catch(() => undefined);
    });
    await settle();
    expect(saves).toHaveLength(1);
    expect(approvals).toHaveLength(0);
  });

  it("approves without saving when nothing changed", async () => {
    const { hook, saves, approvals } = setup();
    act(() => {
      void hook.result.current.approve().catch(() => undefined);
    });
    await settle();
    expect(saves).toHaveLength(0);
    expect(approvals[0].args.slice(0, 2)).toEqual(["r-1", 3]);
  });

  it("keeps one key while the same approval is retried; a new version gets a new one", async () => {
    const { hook, saves, approvals, typeQty } = setup();
    const attempt = async () => {
      act(() => {
        void hook.result.current.approve().catch(() => undefined);
      });
      await settle();
    };
    await attempt();
    await act(async () => approvals[0].d.reject(new TypeError("network down")));
    await settle();
    await attempt();
    expect(approvals).toHaveLength(2);
    expect(approvals[1].args[2]).toBe(approvals[0].args[2]);
    await act(async () => approvals[1].d.reject(new QuoteRefusal(409, "missing_recipient", "Falta el destinatario")));
    await settle();
    typeQty("2");
    await attempt();
    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(approvals).toHaveLength(3);
    expect(approvals[2].args[1]).toBe(4);
    expect(approvals[2].args[2]).not.toBe(approvals[0].args[2]);
  });

  it("a refusal because the prices moved since the save saves again and approves once more", async () => {
    const { hook, saves, approvals } = setup();
    let error: unknown = "none";
    act(() => {
      void hook.result.current.approve().catch((e) => (error = e));
    });
    await settle();
    await act(async () => approvals[0].d.reject(new QuoteRefusal(409, "draft_needs_saving", "Guarda el borrador de nuevo")));
    await settle();
    expect(saves).toHaveLength(1);
    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(approvals).toHaveLength(2);
    expect(approvals[1].args[1]).toBe(4);
    await act(async () => approvals[1].d.reject(new QuoteRefusal(409, "draft_needs_saving", "Guarda el borrador de nuevo")));
    await settle();
    expect(saves).toHaveLength(1); // only once
    expect((error as QuoteRefusal).code).toBe("draft_needs_saving");
  });

  it("does not approve when the save before it hits a conflict", async () => {
    const { hook, saves, approvals, typeQty } = setup();
    typeQty("2");
    let error: unknown;
    act(() => {
      void hook.result.current.approve().catch((e) => (error = e));
    });
    await settle();
    await act(async () => saves[0].d.reject(new QuoteRefusal(409, "stale_version", "x")));
    await settle();
    expect(approvals).toHaveLength(0);
    expect((error as QuoteRefusal).code).toBe("stale_version");
  });
});

describe("useQuoteDraft — leaving the page (Review Focus 5)", () => {
  it("flushes a pending save when the editor unmounts", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    hook.unmount();
    await settle();
    expect(saves).toHaveLength(1);
    expect(saves[0].args[2].items[0].quantity).toBe("2");
  });

  it("warns on beforeunload while dirty", async () => {
    const { typeQty } = setup();
    expect(fireBeforeUnload()).toBe(false);
    typeQty("2");
    expect(fireBeforeUnload()).toBe(true);
  });

  it("warns on beforeunload while the timer's save is in flight, and not once it is saved", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(1); // started by the autosave timer, before any beforeunload
    expect(hook.result.current.state.saveState).toBe("saving");
    expect(fireBeforeUnload()).toBe(true);
    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(saves).toHaveLength(1);
    expect(fireBeforeUnload()).toBe(false);
  });

  it("warns on beforeunload during a conflict, since the local edits will be discarded", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.reject(new QuoteRefusal(409, "stale_version", "x")));
    await settle();
    expect(hook.result.current.state.saveState).toBe("conflict");
    expect(hook.result.current.pending).toBe(true);
    expect(fireBeforeUnload()).toBe(true);
    expect(saves).toHaveLength(1); // the warning sends nothing
  });

  it("an unmount during a save with a queued follow-up still sends the follow-up", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    typeQty("3");
    hook.unmount();
    await settle();
    expect(saves).toHaveLength(1);
    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    expect(saves).toHaveLength(2);
    expect(saves[1].args[1]).toBe(4);
    expect(saves[1].args[2].items[0].quantity).toBe("3");
  });
});

describe("useQuoteDraft — read-only revisions", () => {
  it("ignores edits on a revision that is not an authored draft", async () => {
    const { hook, saves, previews, typeQty } = setup(workspaceFixture({ status: "approved" }));
    expect(hook.result.current.editable).toBe(false);
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(0);
    expect(previews).toHaveLength(0);
    expect(hook.result.current.state.saveState).toBe("idle");
  });
});

describe("useQuoteDraft — incomplete drafts", () => {
  it("does not preview or save a draft the API would refuse, and says why in Spanish", async () => {
    const { hook, saves, previews, edit } = setup();
    edit(addFreeLine);
    await advance(SAVE_DELAY_MS * 2);
    expect(previews).toHaveLength(0);
    expect(saves).toHaveLength(0);
    expect(hook.result.current.state.saveState).toBe("dirty");
    expect(hook.result.current.invalid).toEqual([
      { index: 2, field: "items.description", message_es: "Escribe la descripción de la línea 3." },
    ]);
    expect(hook.result.current.pending).toBe(true);
    expect(fireBeforeUnload()).toBe(true);

    let flushed: unknown;
    await act(async () => {
      flushed = await hook.result.current.flush();
    });
    expect(flushed).toEqual({ ok: false, version: 3 });
    expect(saves).toHaveLength(0);

    edit((d) => updateItem(d, 2, { description: "Instalación" }));
    expect(hook.result.current.invalid).toEqual([]);
    await advance(SAVE_DELAY_MS);
    expect(previews).toHaveLength(1);
    expect(saves).toHaveLength(1);
    expect(saves[0].args[2].items[2].description).toBe("Instalación");
  });

  it("approve refuses an incomplete draft with the first problem, without calling the API", async () => {
    const { hook, saves, approvals, edit } = setup();
    edit((d) => updateItem(d, 0, { quantity: "" }));
    let error: unknown;
    await act(async () => {
      await hook.result.current.approve().catch((e) => (error = e));
    });
    expect(saves).toHaveLength(0);
    expect(approvals).toHaveLength(0);
    expect((error as Error).message).toMatch(/cantidad/i);
  });

  it("an unmount with an incomplete draft sends nothing", async () => {
    const { hook, saves, edit } = setup();
    edit(addFreeLine);
    hook.unmount();
    await settle();
    expect(saves).toHaveLength(0);
  });
});

describe("useQuoteDraft — older prices never win", () => {
  it("a preview of an older draft answering after a flushed save keeps the save's prices", async () => {
    const { hook, saves, previews, typeQty } = setup();
    typeQty("2");
    await advance(PREVIEW_DELAY_MS);
    expect(previews).toHaveLength(1); // prices quantity 2, still in flight
    typeQty("3");
    act(() => void hook.result.current.flush());
    await settle();
    expect(saves[0].args[2].items[0].quantity).toBe("3");
    await act(async () => saves[0].d.resolve(saved(4, "333")));
    await settle();
    await act(async () => previews[0].d.resolve({ computed: computedFixture("222") }));
    await settle();
    expect(hook.result.current.state.computed?.totals.net).toBe("333");
    expect(hook.result.current.state.saveState).toBe("saved");
  });
});

describe("useQuoteDraft — a new workspace from the page", () => {
  it("does not reset unsaved edits for the same revision at a version we already have", async () => {
    const { hook, saves, typeQty } = setup();
    typeQty("2");
    hook.rerender({ ws: workspaceFixture({ version: 3 }) });
    expect(hook.result.current.state.draft.items[0].quantity).toBe("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.resolve(saved(4)));
    await settle();
    hook.rerender({ ws: workspaceFixture({ version: 3 }) }); // older than what we saved
    expect(hook.result.current.state).toMatchObject({ version: 4, saveState: "saved" });
    expect(hook.result.current.state.draft.items[0].quantity).toBe("2");
  });

  it("resets to a newer workspace when nothing is pending", async () => {
    const { hook } = setup();
    hook.rerender({ ws: workspaceFixture({ status: "approved", version: 5 }) });
    expect(hook.result.current.state.version).toBe(5);
    expect(hook.result.current.editable).toBe(false);
  });
});

describe("useQuoteDraft — retry and reload", () => {
  it("retry() sends the failed save again with the same key", async () => {
    const { hook, saves, typeQty } = setup();
    let idle: unknown;
    await act(async () => {
      idle = await hook.result.current.retry();
    });
    expect(idle).toEqual({ ok: true, version: 3 });
    expect(saves).toHaveLength(0);

    typeQty("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.reject(new QuoteRefusal(503, "backend_unavailable", "no disponible")));
    await settle();
    expect(hook.result.current.state.saveState).toBe("error");
    let result: unknown;
    act(() => {
      void hook.result.current.retry().then((r) => (result = r));
    });
    await settle();
    expect(saves).toHaveLength(2);
    expect(saves[1].args[3]).toBe(saves[0].args[3]);
    await act(async () => saves[1].d.resolve(saved(4)));
    await settle();
    expect(result).toEqual({ ok: true, version: 4 });
    expect(hook.result.current.state.saveState).toBe("saved");
  });

  it("reload() while a save is in flight ignores that save's late answer", async () => {
    const { hook, saves, reloads, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    act(() => void hook.result.current.reload());
    await settle();
    await act(async () => reloads[0].resolve(workspaceFixture({ version: 8 })));
    await settle();
    await act(async () => saves[0].d.resolve(saved(4, "444")));
    await settle();
    expect(hook.result.current.state).toMatchObject({ version: 8, saveState: "idle" });
    expect(hook.result.current.state.computed?.totals.net).toBe("100000");

    typeQty("5");
    await advance(SAVE_DELAY_MS);
    expect(saves).toHaveLength(2);
    expect(saves[1].args[1]).toBe(8);
  });
});

describe("useQuoteDraft — rows and their prices", () => {
  it("says the prices are stale from an edit until a preview of that edit answers, and keeps the keys it priced", async () => {
    const { hook, previews, edit } = setup();
    const before = hook.result.current.state.draft.items.map((i) => i.uid);
    expect(hook.result.current.stale).toBe(false);
    edit((d) => removeItem(d, 0));
    expect(hook.result.current.stale).toBe(true);
    await advance(PREVIEW_DELAY_MS);
    expect(previews[0].draft.items.some((i) => "uid" in i)).toBe(false);
    await act(async () => previews[0].d.resolve({ computed: computedFixture("9") }));
    await settle();
    expect(hook.result.current.stale).toBe(false);
    expect(hook.result.current.state.computedKeys).toEqual([before[1]]);
  });

  it("gives a line added without a uid one", () => {
    const { hook, edit } = setup();
    edit((d) => ({ ...d, items: [...d.items, { description: "x", quantity: "1", margin_mode: "markup" }] }));
    expect(typeof hook.result.current.state.draft.items[2].uid).toBe("string");
  });
});

describe("useQuoteDraft — a reload keeps one set of row ids", () => {
  it("after «Ver cambios», an edit keeps every row's uid and its priced line", async () => {
    const { hook, saves, reloads, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.reject(new QuoteRefusal(409, "stale_version", "x")));
    await settle();
    act(() => void hook.result.current.reload());
    await act(async () => reloads[0].resolve(workspaceFixture({ version: 8 })));
    await settle();
    const after = hook.result.current.state.draft.items.map((i) => i.uid);
    expect(hook.result.current.state.computedKeys).toEqual(after);
    typeQty("5");
    expect(hook.result.current.state.draft.items.map((i) => i.uid)).toEqual(after);
    expect(hook.result.current.state.computedKeys).toEqual(after);
  });

  it("a new workspace from the page keeps one set of uids too", async () => {
    const { hook, typeQty } = setup();
    hook.rerender({ ws: workspaceFixture({ version: 9 }) });
    const after = hook.result.current.state.draft.items.map((i) => i.uid);
    expect(hook.result.current.state.computedKeys).toEqual(after);
    typeQty("4");
    expect(hook.result.current.state.draft.items.map((i) => i.uid)).toEqual(after);
  });
});
