import { useState, type ReactNode } from "react";
import { Badge, Button, Skeleton, toast } from "../ui";
import { crmHash, type CrmSection } from "../crmRoute";
import { ClientSection } from "../quotes/ClientSection";
import { isEditable } from "../quotes/draftEditor";
import { ProductsSection } from "../quotes/ProductsSection";
import { QuoteRefusal, fetchQuoteWorkspace, type QuoteWorkspace } from "../quotes/quoteApi";
import { quoteLabel } from "../quotes/quoteLabel";
import { SaveStatus } from "../quotes/SaveStatus";
import { useQuoteDraft } from "../quotes/useQuoteDraft";
import { useResource } from "../useResource";

/**
 * «Cotizador»: the workspace of one quote, at `#/crm/cotizar/<quoteId>`. It is not in the menu —
 * it opens from a case. Only sales and administration may quote (the API answers 403
 * `role_may_not_view_costs` to anyone else), and the page says so instead of showing a generic error.
 * Every number on the page comes from the API; the browser never prices.
 *
 * An authored draft is edited in place (`useQuoteDraft`: live preview, autosave); the header says
 * whether it is saved. «Vista cliente» (B7) hides costs and margins for screen sharing.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

const STATUS: Record<string, { label: string; tone: "warn" | "good" | "info" | "neutral" }> = {
  draft: { label: "Borrador", tone: "warn" },
  approved: { label: "Aprobada", tone: "good" },
  sent: { label: "Enviada", tone: "info" },
  void: { label: "Anulada", tone: "neutral" },
};

function BackToCases({ navigate }: { navigate: Navigate }) {
  return (
    <a
      href={crmHash("oportunidades")}
      onClick={(e) => {
        e.preventDefault();
        navigate("oportunidades");
      }}
      className="mt-3 inline-flex min-h-[44px] items-center rounded-md border border-line bg-canvas-raised px-3 text-[13px] font-medium text-brand-700 hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      Volver a Oportunidades
    </a>
  );
}

function NotFound({ navigate }: { navigate: Navigate }) {
  return (
    <div role="status" className="rounded-lg border border-line bg-canvas-sunken/70 px-4 py-4">
      <p className="text-[13px] font-semibold text-ink">Esta cotización no existe.</p>
      <p className="mt-1 text-xs text-ink-muted">Puede que el enlace esté incompleto o que la cotización ya no esté disponible.</p>
      <BackToCases navigate={navigate} />
    </div>
  );
}

/** The page's shape while it loads: the header, then the two columns the editor will fill. */
function CotizadorSkeleton() {
  return (
    <div className="space-y-4" data-testid="cotizador-skeleton">
      <div className="space-y-2">
        <div className="crm-skeleton h-4 w-64 rounded-md" />
        <div className="crm-skeleton h-8 w-72 rounded-md" />
      </div>
      <Skeleton rows={4} />
    </div>
  );
}

export function CotizadorPage({ quoteId, navigate }: { quoteId: string | null; navigate: Navigate }) {
  const [state, reload] = useResource(
    () => (quoteId ? fetchQuoteWorkspace(quoteId) : Promise.reject(new Error("quote_id_missing"))),
    [quoteId],
  );
  if (!quoteId) return <NotFound navigate={navigate} />;
  if (state.kind === "loading") return <CotizadorSkeleton />;
  if (state.kind === "permission") {
    return (
      <div role="alert" className="rounded-lg border border-warn/30 bg-warn-bg px-4 py-4">
        <p className="text-[13px] font-semibold text-warn">Sólo ventas y administración pueden cotizar.</p>
        <p className="mt-1 text-xs text-ink-muted">Si necesitas cotizar, pide a administración que cambie tu rol.</p>
      </div>
    );
  }
  if (state.kind === "unavailable") return <NotFound navigate={navigate} />;
  if (state.kind === "error") {
    return (
      <div role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-4 py-4">
        <p className="text-[13px] font-semibold text-bad">No se pudo abrir la cotización</p>
        <p className="mt-1 break-words text-xs text-ink-muted">{state.message}</p>
        <button
          type="button"
          onClick={reload}
          className="mt-2 min-h-[44px] rounded-md bg-ink px-4 text-xs font-medium text-white hover:bg-black focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          Reintentar
        </button>
      </div>
    );
  }
  return <CotizadorWorkspace workspace={state.data} navigate={navigate} />;
}

/** A save refused because of whom the quote goes to: said under «Para». */
function isRecipientRefusal(code: string | null): boolean {
  return !!code && (code.startsWith("recipient_") || code === "labdelivery_refused");
}

function CotizadorWorkspace({ workspace, navigate }: { workspace: QuoteWorkspace; navigate: Navigate }) {
  const editor = useQuoteDraft(workspace);
  const [clientView, setClientView] = useState(false);
  const { state } = editor;
  const authored = isEditable(state.workspace);
  const disabled = !editor.editable;
  const refusal = state.error instanceof QuoteRefusal ? state.error : null;
  const recipientRefusal = refusal && isRecipientRefusal(refusal.code) ? refusal.message : null;
  // Only an API envelope's message is the server's words; a bare body (a proxy error page) is not shown.
  const saveMessage = recipientRefusal ? "revisa «Para»" : refusal?.code ? refusal.message : null;
  return (
    <div className="space-y-[18px]">
      <CotizadorHeader
        workspace={state.workspace}
        navigate={navigate}
        status={
          authored ? (
            <SaveStatus
              saveState={state.saveState}
              lastSavedAt={state.lastSavedAt}
              incomplete={editor.invalid.length > 0}
              message={saveMessage}
              onRetry={() => void editor.retry()}
            />
          ) : null
        }
      />
      {state.saveState === "conflict" ? <ConflictBanner onReload={editor.reload} /> : null}
      {authored ? (
        <div className="flex flex-wrap items-start gap-[18px]">
          <div className="flex min-w-0 flex-[999_1_760px] flex-col gap-[18px]">
            <ClientSection
              workspace={state.workspace}
              draft={state.draft}
              invalid={editor.invalid}
              disabled={disabled}
              refusal={recipientRefusal}
              edit={editor.edit}
            />
            <ProductsSection
              workspace={state.workspace}
              draft={state.draft}
              computed={state.computed}
              computedKeys={state.computedKeys}
              stale={editor.stale}
              invalid={editor.invalid}
              disabled={disabled}
              clientView={clientView}
              onClientViewChange={setClientView}
              edit={editor.edit}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** B3: someone else saved this draft; nothing more is sent until the operator loads their version. */
function ConflictBanner({ onReload }: { onReload: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warn/30 bg-warn-bg px-4 py-3">
      <div>
        <p className="text-[13px] font-semibold text-warn">Otra persona cambió este borrador.</p>
        <p className="mt-0.5 text-xs text-ink-muted">Tus últimos cambios no se guardaron. Abre la versión guardada para seguir.</p>
      </div>
      <Button
        variant="primary"
        busy={busy}
        busyLabel="Abriendo…"
        className="min-h-[44px] px-4"
        onClick={() => {
          setBusy(true);
          onReload()
            .catch((e: unknown) => toast(e instanceof Error ? e.message : "No se pudo abrir la versión guardada.", "bad"))
            .finally(() => setBusy(false));
        }}
      >
        Ver cambios
      </Button>
    </div>
  );
}

function CotizadorHeader({ workspace, navigate, status }: { workspace: QuoteWorkspace; navigate: Navigate; status: ReactNode }) {
  const { quote, case: opportunity, current, revisions } = workspace;
  const badge = STATUS[current?.status ?? revisions[revisions.length - 1]?.status ?? "draft"] ?? STATUS.draft;
  const label = quoteLabel({ label: current?.label, quote_number: quote.quote_number, revision_no: current?.revision_no });
  const caseTitle = opportunity.title || opportunity.institution?.name || "la oportunidad";
  const caseHref = crmHash("oportunidades", quote.opportunity_id);
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <a
          href={caseHref}
          onClick={(e) => {
            e.preventDefault();
            navigate("oportunidades", quote.opportunity_id);
          }}
          className="inline-block max-w-full truncate text-[13px] font-medium text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          ← {caseTitle}
        </a>
        <div className="mt-1.5 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-ink">
            Cotización <span className="font-mono font-medium tabular-nums">{label}</span>
          </h1>
          <Badge tone={badge.tone}>{badge.label}</Badge>
          {status}
        </div>
      </div>
      {/* Later tasks put the actions here (preview, approve, send…). */}
      <div className="flex flex-wrap gap-2" data-testid="cotizador-actions" />
    </header>
  );
}
