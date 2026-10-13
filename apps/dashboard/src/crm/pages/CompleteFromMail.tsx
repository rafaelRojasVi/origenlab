import { useEffect, useState } from "react";
import { useMayRunCaseCommands } from "../caseCommands";
import { fetchCaseMailDocuments } from "../crmApi";
import type { OpportunityCardData } from "../crmTypes";
import { institutionFromQuoteFile, quotePdfOf, type QuotePdf } from "../sentQuote";
import { Button } from "../ui";
import { CompleteSentQuote } from "./CompleteSentQuote";

/** «CN09876» sent in 2026 → «09876-26»: shown to the person; the API derives the number it records. */
export function displayQuoteNumber(token: string, sentAt: string | null): string {
  const digits = token.replace(/\D/g, "");
  const year = sentAt ? new Date(sentAt).getUTCFullYear() % 100 : new Date().getUTCFullYear() % 100;
  return `${digits.padStart(5, "0").slice(-5)}-${String(year).padStart(2, "0")}`;
}

/**
 * The case panel's «Completar con la cotización del correo» (2026-10-13): an open case with no quote
 * whose emails carry one sent quote PDF not yet recorded gets one button that confirms the
 * institution and leaves the case in «Enviada» with the quote (`CompleteSentQuote`).
 */
export function CompleteFromMail({ card, onChanged }: { card: OpportunityCardData; onChanged: () => void }) {
  const mayDecide = useMayRunCaseCommands();
  const eligible = mayDecide && card.quotes.length === 0 && card.closed_at == null && typeof card.version === "number"
    && ["lead", "qualifying", "qualified", "quoting", "negotiating"].includes(card.stage);
  const [pdf, setPdf] = useState<(QuotePdf & { sentAt: string | null }) | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!eligible) return;
    let live = true;
    fetchCaseMailDocuments(card.opportunity_id)
      .then((r) => {
        const found = quotePdfOf(r.messages);
        const sentAt = found ? r.messages.find((m) => m.source_record_id === found.sourceRecordId)?.sent_at ?? null : null;
        if (live) setPdf(found ? { ...found, sentAt } : null);
      })
      .catch(() => live && setPdf(null));
    return () => { live = false; };
  }, [eligible, card.opportunity_id]);

  if (!eligible || !pdf) return null;
  const number = displayQuoteNumber(pdf.token, pdf.sentAt);
  return (
    <div className="space-y-2" data-testid="complete-from-mail">
      {open ? (
        <CompleteSentQuote
          sourceRecordId={pdf.sourceRecordId}
          quoteNumber={number}
          suggestion={institutionFromQuoteFile(pdf.filename)}
          opportunity={{ id: card.opportunity_id, version: card.version as number }}
          onDone={() => { setOpen(false); onChanged(); }}
        />
      ) : (
        <Button variant="primary" aria-label={`Completar con la cotización ${number}`} onClick={() => setOpen(true)}>
          Completar con la cotización {number}
        </Button>
      )}
    </div>
  );
}
