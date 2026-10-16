/**
 * «Cotizar» from Hoy, from a request's card and from the case: open the case's open draft if it
 * has one, else create a quote (`create-quote`) and open it in the Cotizador.
 *
 * "Open draft" = a quote of the case whose newest revision is a draft or in review of this app
 * (`origin = 'authored'`); a historical import is never edited here. The pipeline read carries
 * each revision's `status` and `origin`, so no extra read is needed.
 *
 * Each click is one command with its own `Idempotency-Key`; a click while one is in flight does
 * nothing. A page whose copy of the case predates a quote created from it in this session opens
 * that quote rather than minting a second number. The API decides whether the case may get a
 * quote (closed, numbering not configured…); its reason is said in a toast, in Spanish.
 *
 * Callers offer this only to sales and admin with the commands enabled (`useMayRunCaseCommands`);
 * anyone else keeps the old behaviour (open the case).
 */
import { useCallback, useRef, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import { crmHash } from "../crmRoute";
import type { OpportunityCardData, QuoteCard, RevisionCard } from "../crmTypes";
import { toast } from "../ui";
import { QuoteRefusal, createQuote, newQuoteCommandKey } from "./quoteApi";

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

/** The case's quote whose newest revision is an authored draft or in review; null when none. */
export function openDraftOf(card: OpportunityCardData): QuoteCard | null {
  return (
    card.quotes.find((q) => {
      const r = newestRevision(q);
      return !!r && r.origin === "authored" && OPEN.has(r.status);
    }) ?? null
  );
}

/** Quotes created from this browser tab, by case, until the page's copy of the case shows them. */
const createdHere = new Map<string, string>();

/** For tests: forget the quotes created in this session. */
export function forgetCreatedQuotes(): void {
  createdHere.clear();
}

/** The quote «Cotizar» opens without creating one: the open draft, else one just created here. */
export function quoteToOpen(card: OpportunityCardData): string | null {
  const draft = openDraftOf(card);
  if (draft) return draft.quote_id;
  const made = createdHere.get(card.opportunity_id);
  // Once the page shows that quote, its own revisions decide (a voided one gets a successor).
  return made && !card.quotes.some((q) => q.quote_id === made) ? made : null;
}

/** English API reasons said in Spanish; any other refusal is said in the API's own words. */
const SPANISH: Record<string, string> = {
  case_closed: "Este caso está cerrado; no recibe cotizaciones nuevas.",
  case_not_found: "Este caso ya no existe.",
  quote_number_taken: "Ese número de cotización se acaba de usar; vuelve a intentarlo.",
  role_may_not_view_costs: "Sólo ventas y administración pueden cotizar.",
};

const FALLBACK = "No se pudo crear la cotización.";

/** What to say when `create-quote` failed. A body that is not the API's envelope is not shown. */
export function createRefusalText(e: unknown): string {
  if (e instanceof QuoteRefusal && e.code) return SPANISH[e.code] ?? (e.message.trim() || FALLBACK);
  return FALLBACK;
}

const toCotizador = (quoteId: string) => {
  window.location.hash = crmHash("cotizar", quoteId);
  window.scrollTo?.({ top: 0 });
};

/**
 * `run(card)` opens or creates the case's quote, then calls `go(quoteId)` (default: the
 * Cotizador's address). `busy` is true while a quote is being created.
 */
export function useOpenOrCreateQuote(go: (quoteId: string) => void = toCotizador): {
  busy: boolean;
  run: (card: OpportunityCardData) => Promise<void>;
} {
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const run = useCallback(
    async (card: OpportunityCardData) => {
      if (inFlight.current) return;
      const existing = quoteToOpen(card);
      if (existing) {
        go(existing);
        return;
      }
      inFlight.current = true;
      setBusy(true);
      let quoteId: string | null = null;
      try {
        const made = await createQuote(card.opportunity_id, newQuoteCommandKey());
        createdHere.set(card.opportunity_id, made.quote_id);
        quoteId = made.quote_id;
      } catch (e) {
        toast(createRefusalText(e), "bad");
      } finally {
        inFlight.current = false;
        setBusy(false);
      }
      if (quoteId) go(quoteId);
    },
    [go],
  );
  return { busy, run };
}
