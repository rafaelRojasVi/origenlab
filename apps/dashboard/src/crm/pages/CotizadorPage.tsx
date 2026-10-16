import { Badge, Skeleton } from "../ui";
import { crmHash, type CrmSection } from "../crmRoute";
import { fetchQuoteWorkspace, type QuoteWorkspace } from "../quotes/quoteApi";
import { quoteLabel } from "../quotes/quoteLabel";
import { useResource } from "../useResource";

/**
 * «Cotizador»: the workspace of one quote, at `#/crm/cotizar/<quoteId>`. It is not in the menu —
 * it opens from a case. Only sales and administration may quote (the API answers 403
 * `role_may_not_view_costs` to anyone else), and the page says so instead of showing a generic error.
 * Every number on the page comes from the API; the browser never prices.
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
  return <CotizadorHeader workspace={state.data} navigate={navigate} />;
}

function CotizadorHeader({ workspace, navigate }: { workspace: QuoteWorkspace; navigate: Navigate }) {
  const { quote, case: opportunity, current, revisions } = workspace;
  const status = STATUS[current?.status ?? revisions[revisions.length - 1]?.status ?? "draft"] ?? STATUS.draft;
  const label = quoteLabel({ label: current?.label, quote_number: quote.quote_number, revision_no: current?.revision_no });
  const caseTitle = opportunity.title || opportunity.institution?.name || "la oportunidad";
  const caseHref = crmHash("oportunidades", quote.opportunity_id);
  return (
    <div className="space-y-4">
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
            <Badge tone={status.tone}>{status.label}</Badge>
          </div>
        </div>
        {/* Later tasks put the actions here (preview, approve, send…). */}
        <div className="flex flex-wrap gap-2" data-testid="cotizador-actions" />
      </header>
    </div>
  );
}
