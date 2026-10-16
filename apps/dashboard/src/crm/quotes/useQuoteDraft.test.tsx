import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { updateItem } from "./draftEditor";
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
  const typeQty = (qty: string) => act(() => hook.result.current.edit((d) => updateItem(d, 0, { quantity: qty })));
  return { ...fake, hook, typeQty };
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
  it("a 409 stale_version stops autosave; reload discards local edits and resumes (B3)", async () => {
    const { hook, saves, reloads, typeQty } = setup();
    typeQty("2");
    await advance(SAVE_DELAY_MS);
    await act(async () => saves[0].d.reject(new QuoteRefusal(409, "stale_version", "the draft was modified since you loaded it")));
    await settle();
    expect(hook.result.current.state.saveState).toBe("conflict");

    typeQty("9");
    await advance(SAVE_DELAY_MS * 3);
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

  it("warns on beforeunload only while dirty or saving", async () => {
    const { saves, typeQty } = setup();
    const fire = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(fire()).toBe(false);
    typeQty("2");
    expect(fire()).toBe(true);
    await advance(SAVE_DELAY_MS);
    expect(fire()).toBe(true); // saving
    await act(async () => saves[saves.length - 1].d.resolve(saved(4)));
    await settle();
    expect(fire()).toBe(false);
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
