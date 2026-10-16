/**
 * «Cotizar» from Hoy, from a request's card and from the case: open the case's open draft if it
 * has one, else create a quote (`create-quote`) and open it in the Cotizador.
 *
 * "Open draft" = a quote of the case whose newest revision is a draft or in review of this app
 * (`origin = 'authored'`); a historical import is never edited here. The pipeline read carries
 * each revision's `status` and `origin`, so no extra read is needed.
 *
 * One case, one create at a time: a click while a create for the same case is in flight (from
 * this button, the case's card, its drawer or Hoy) does nothing. A create keeps its
 * `Idempotency-Key` until the API answers for certain (the quote, or a refusal with its code);
 * after a network failure, a gateway error or a body that is not the API's, the next click
 * repeats the same command, so the server never mints a second number for it. A page whose copy
 * of the case predates a quote created from it in this session opens that quote. The API decides
 * whether the case may get a quote (closed, numbering not configured…); its reason is said in a
 * toast, in Spanish. «Still running» (`command_in_progress`, `idempotency_conflict`) keeps the
 * key. A button that is gone when the answer comes (the user went elsewhere) does not navigate.
 *
 * Callers offer this only to sales and admin with the commands enabled (`useMayRunCaseCommands`);
 * anyone else keeps the old behaviour (open the case).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import { crmHash } from "../crmRoute";
import type { OpportunityCardData, QuoteCard, RevisionCard } from "../crmTypes";
import { toast } from "../ui";
import { QuoteRefusal, createQuote, newQuoteCommandKey } from "./quoteApi";
import { quoteRefusalText } from "./quoteRefusal";

const OPEN = new Set(["draft", "in_review"]);

function newestRevision(q: QuoteCard): RevisionCard | null {
  return q.revisions.reduce<RevisionCard | null>((a, r) => (a === null || r.revision_no > a.revision_no ? r : a), null);
}

/** A quote made in this app (it has at least one authored revision): the Cotizador can open it. */
export function isAuthoredQuote(q: QuoteCard): boolean {
  return q.revisions.some((r) => r.origin === "authored");
}

/**
 * «Abrir en Cotizador» on a quote of this app: a read, so the role is enough (the quote API is
 * sales/admin only; anyone else would land on «Sólo ventas y administración…»).
 */
export function useMayOpenCotizador(): boolean {
  const { session } = useAuthSession();
  return session.kind === "signed_in" && ROLES_THAT_DECIDE_CASES.has(session.operator.role);
}

/** «00042-26» → 26·100000 + 42, so a later year sorts after; an unusual number sorts first. */
function numberRank(q: QuoteCard): number {
  const m = /^(\d{1,5})-(\d{2})$/.exec(q.quote_number.trim());
  return m ? Number(m[2]) * 100_000 + Number(m[1]) : -1;
}

/** The case's newest quote whose newest revision is an authored draft or in review; null when none. */
export function openDraftOf(card: OpportunityCardData): QuoteCard | null {
  const open = card.quotes.filter((q) => {
    const r = newestRevision(q);
    return !!r && r.origin === "authored" && OPEN.has(r.status);
  });
  return open.reduce<QuoteCard | null>((a, q) => (a === null || numberRank(q) > numberRank(a) ? q : a), null);
}

/** Quotes created from this browser tab, by case, until the page's copy of the case shows them. */
const createdHere = new Map<string, string>();

/** Cases with a create in flight, from any button. */
const creating = new Set<string>();
/** A create's key, by case, until the API answered it for certain. */
const pendingKeys = new Map<string, string>();

/** For tests: forget the quotes created, in flight and pending in this session. */
export function forgetCreatedQuotes(): void {
  createdHere.clear();
  creating.clear();
  pendingKeys.clear();
}

/** The quote «Cotizar» opens without creating one: the open draft, else one just created here. */
export function quoteToOpen(card: OpportunityCardData): string | null {
  const draft = openDraftOf(card);
  if (draft) return draft.quote_id;
  const made = createdHere.get(card.opportunity_id);
  // Once the page shows that quote, its own revisions decide (a voided one gets a successor).
  return made && !card.quotes.some((q) => q.quote_id === made) ? made : null;
}

const FALLBACK = "No se pudo crear la cotización.";

/** What to say when `create-quote` failed, in Spanish (`quoteRefusalText`). A body that is not the API's envelope is not shown. */
export function createRefusalText(e: unknown): string {
  return quoteRefusalText(e, FALLBACK);
}

const toCotizador = (quoteId: string) => {
  window.location.hash = crmHash("cotizar", quoteId);
  window.scrollTo?.({ top: 0 });
};

/** The same command may still be running on the server: retrying it must keep its key. */
const STILL_RUNNING = new Set(["command_in_progress", "idempotency_conflict"]);

/**
 * The API answered for certain: a refusal of its own (4xx with a code). Anything else may have
 * run, and so may a create the API says is still in progress under this key.
 */
function definitive(e: unknown): boolean {
  return e instanceof QuoteRefusal && !!e.code && !STILL_RUNNING.has(e.code) && e.status >= 400 && e.status < 500;
}

/**
 * `run(card)` opens or creates the case's quote, then calls `go(quoteId)` (default: the
 * Cotizador's address). `busy` is true while this button's create is in flight.
 */
export function useOpenOrCreateQuote(go: (quoteId: string) => void = toCotizador): {
  busy: boolean;
  run: (card: OpportunityCardData) => Promise<void>;
} {
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const run = useCallback(
    async (card: OpportunityCardData) => {
      const id = card.opportunity_id;
      if (creating.has(id)) return;
      const existing = quoteToOpen(card);
      if (existing) {
        go(existing);
        return;
      }
      creating.add(id);
      setBusy(true);
      let key = pendingKeys.get(id);
      if (!key) {
        key = newQuoteCommandKey();
        pendingKeys.set(id, key);
      }
      let quoteId: string | null = null;
      try {
        const made = await createQuote(id, key);
        pendingKeys.delete(id);
        createdHere.set(id, made.quote_id);
        quoteId = made.quote_id;
      } catch (e) {
        if (definitive(e)) pendingKeys.delete(id);
        toast(createRefusalText(e), "bad");
      } finally {
        creating.delete(id);
        if (mounted.current) setBusy(false);
      }
      if (quoteId && mounted.current) go(quoteId);
    },
    [go],
  );
  return { busy, run };
}
