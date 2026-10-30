import { useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import { crmHash, noticeIdOfRoute, type CrmSection } from "../crmRoute";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, PageHeader, ResourceGate, Segmented, Skeleton, type Tone } from "../ui";
import { useResource, type ResourceState } from "../useResource";
import { DiscoveredTenderPage } from "./DiscoveredTenderPage";
import { DiscoveredTenders } from "./DiscoveredTenders";
import { NewTenderDialog } from "./NewTenderDialog";
import { TenderPage } from "./TenderPage";
import { factsOfRow, tenderSteps, type StepState } from "./TenderSteps";
import { TENDER_STATUSES, fetchTenders, type TenderList, type TenderRow, type TenderStatus } from "./tenderApi";
import { closingLabel, closingWhen } from "./tenderTime";

/**
 * «Licitaciones» opens on «En Mercado Público» (`DiscoveredTenders`): the open tenders that fit the
 * catalog, each with «Preparar». «Mis licitaciones» (its tab says how many are in play) is the
 * tenders being prepared, as a list a salesperson reads at a glance — code, title and institution
 * (marked «por confirmar» when the machine proposed it); when it closes and how long is left
 * (Santiago time, a dot when it is two days or less); how far the bid is — the tender page's own
 * six steps, as «3 de 6» and the next one (sales and admin; a viewer sees no bidder data); the
 * amount (neto; «estimado» when it comes from the notice) and the estado. The list is read once
 * and kept in page memory; the chips (count per estado) filter it here. A row opens the tender's
 * page (`TenderPage`). «Nueva licitación» (sales and admin) opens one from a Mercado Público code or
 * link. Every figure is the API's.
 *
 * At phone width a row stacks its cells; nothing scrolls sideways.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

const STATUS_LABEL: Record<TenderStatus, string> = {
  preparando: "Preparando",
  postulada: "Postulada",
  adjudicada: "Adjudicada",
  perdida: "Perdida",
};
const STATUS_TONE: Record<TenderStatus, Tone> = { preparando: "info", postulada: "brand", adjudicada: "good", perdida: "neutral" };
const EMPTY_FILTERED = "No hay licitaciones en este estado.";
const ZERO: TenderList["counts"] = { preparando: 0, postulada: 0, adjudicada: 0, perdida: 0 };

const URGENT_MS = 2 * 24 * 3_600_000;
const IN_PLAY: readonly TenderStatus[] = ["preparando", "postulada"];

/** The grid shared by the header and the rows from `md` up. */
const GRID = "md:grid md:grid-cols-[minmax(0,2.4fr)_minmax(0,1.1fr)_minmax(0,1.5fr)_minmax(0,1fr)_7rem] md:items-center md:gap-4";

export function LicitacionesPage({
  id = null,
  navigate,
  now,
}: {
  id?: string | null;
  navigate: Navigate;
  /** A fixed clock, for tests. */
  now?: Date;
}) {
  // Keyed by the tender: nothing of A (its state, a read in flight) can render or act under B.
  const noticeId = noticeIdOfRoute(id);
  if (noticeId) return <DiscoveredTenderPage key={noticeId} noticeId={noticeId} navigate={navigate} now={now} />;
  if (id) return <TenderPage key={id} id={id} navigate={navigate} now={now} />;
  return <TenderList navigate={navigate} now={now} />;
}

type View = "mercado" | "mias";

function TenderList({ navigate, now }: { navigate: Navigate; now?: Date }) {
  const { session } = useAuthSession();
  const mayOpen = session.kind === "signed_in" && ROLES_THAT_DECIDE_CASES.has(session.operator.role);
  const [view, setView] = useState<View>("mercado");
  // The code «Preparar» passed, or "" for an empty «Nueva licitación»; null: the dialog is closed.
  const [asking, setAsking] = useState<string | null>(null);
  // Read here, once, so the tab can say how many are in play before it is opened.
  const [mine, reloadMine] = useResource(fetchTenders);
  const inPlay = mine.kind === "ready" ? IN_PLAY.reduce((n, s) => n + mine.data.counts[s], 0) : 0;
  const at = now ?? new Date();

  return (
    <div className="space-y-4">
      <PageHeader
        title="Licitaciones"
        actions={mayOpen ? <Button variant="primary" onClick={() => setAsking("")}>Nueva licitación</Button> : undefined}
      />
      <Segmented<View>
        label="Vista"
        value={view}
        onChange={setView}
        options={[
          { value: "mercado", label: "En Mercado Público" },
          { value: "mias", label: inPlay > 0 ? `Mis licitaciones (${inPlay})` : "Mis licitaciones" },
        ]}
      />
      {view === "mercado" ? (
        <DiscoveredTenders navigate={navigate} now={at} mayOpen={mayOpen} onPrepare={setAsking} />
      ) : (
        <MyTenders state={mine} reload={reloadMine} navigate={navigate} now={at} />
      )}
      {asking !== null ? (
        <NewTenderDialog navigate={navigate} initialReference={asking} onClose={() => setAsking(null)} />
      ) : null}
    </div>
  );
}

function MyTenders({
  state,
  reload,
  navigate,
  now: at,
}: {
  state: ResourceState<TenderList>;
  reload: () => void;
  navigate: Navigate;
  now: Date;
}) {
  const [status, setStatus] = useState<TenderStatus | null>(null);
  const counts = state.kind === "ready" ? state.data.counts : ZERO;
  const total = TENDER_STATUSES.reduce((n, s) => n + counts[s], 0);

  return (
    <div className="space-y-4">
      <div role="group" aria-label="Filtrar por estado" className="flex flex-wrap gap-1.5">
        {TENDER_STATUSES.map((s) => (
          <Chip key={s} label={STATUS_LABEL[s]} count={counts[s]} pressed={status === s} onClick={() => setStatus(s)} />
        ))}
        <Chip label="Todas" count={total} pressed={status === null} onClick={() => setStatus(null)} />
      </div>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(data) => {
          const shown = data.tenders.filter((t) => status === null || t.status === status);
          return shown.length === 0 ? (
            <EmptyState title={status === null ? "Aún no hay licitaciones. Abre una con su código de Mercado Público." : EMPTY_FILTERED} />
          ) : (
            <section aria-label="Licitaciones" className="overflow-hidden rounded-xl border border-line bg-canvas-raised">
              <div aria-hidden="true" className={`hidden border-b border-line bg-canvas-sunken px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-ink-faint ${GRID}`}>
                <span>Licitación</span>
                <span>Cierre</span>
                <span>Avance</span>
                <span>Monto</span>
                <span>Estado</span>
              </div>
              <ul className="divide-y divide-line">
                {shown.map((t) => (
                  <TenderItem key={t.id} tender={t} bidderReady={data.bidder_ready} now={at} navigate={navigate} />
                ))}
              </ul>
            </section>
          );
        }}
      </ResourceGate>
    </div>
  );
}

function Chip({ label, count, pressed, onClick }: { label: string; count: number; pressed: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`inline-flex min-h-[36px] items-center gap-1.5 rounded-full border px-3 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
        pressed
          ? "border-brand-700 bg-brand-700 text-white"
          : "border-line-strong bg-canvas-raised text-ink hover:border-brand-600 hover:text-accent"
      }`}
    >
      <span>{label}</span>{" "}
      <span className="tabular-nums opacity-80">{count}</span>
    </button>
  );
}

const PROGRESS_TONE: Record<StepState, string> = {
  hecho: "text-good",
  en_curso: "text-info",
  ahora: "text-ink",
  pendiente: "text-ink-muted",
  error: "text-bad",
};

/** How far the bid is: the page's six steps as «3 de 6», a bar, and the next one. */
function Progress({ tender, bidderReady }: { tender: TenderRow; bidderReady: boolean }) {
  const steps = tenderSteps(factsOfRow(tender, bidderReady));
  const done = steps.filter((s) => s.state === "hecho").length;
  const next = steps.find((s) => s.state !== "hecho");
  const label = !next ? "Todo listo" : next.state === "en_curso" ? `${next.title}: ${next.detail}` : next.state === "error" ? `${next.title}: no se pudo` : `Siguiente: ${next.title}`;
  return (
    <span className="flex min-w-0 flex-col gap-1">
      <span className="flex items-center gap-2">
        <span
          role="progressbar"
          aria-label="Avance de la postulación"
          aria-valuemin={0}
          aria-valuemax={steps.length}
          aria-valuenow={done}
          className="h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-canvas-sunken"
        >
          <span className="block h-full rounded-full bg-good" style={{ width: `${(done / steps.length) * 100}%` }} />
        </span>
        <span className="text-[12px] tabular-nums text-ink-muted">
          {done} de {steps.length}
        </span>
      </span>
      <span className={`break-words text-[12px] leading-snug ${PROGRESS_TONE[next?.state ?? "hecho"]}`}>{label}</span>
    </span>
  );
}

function TenderItem({ tender: t, bidderReady, now, navigate }: { tender: TenderRow; bidderReady?: boolean; now: Date; navigate: Navigate }) {
  const amount = t.amount ? formatMoney(String(t.amount.neto), t.amount.currency) : null;
  const left = t.closes_at ? new Date(t.closes_at).getTime() - now.getTime() : Number.NaN;
  const urgent = t.status === "preparando" && left > 0 && left <= URGENT_MS;
  return (
    <li data-testid={`tender-row-${t.id}`}>
      <a
        href={crmHash("licitaciones", t.id)}
        onClick={(e) => {
          e.preventDefault();
          navigate("licitaciones", t.id);
        }}
        className={`flex min-w-0 flex-col gap-2 px-4 py-3 hover:bg-canvas-sunken/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${GRID}`}
      >
        <span className="min-w-0 space-y-0.5">
          {t.code ? <span className="block text-[11px] tabular-nums text-ink-muted">{t.code}</span> : null}
          <span className="block break-words text-[13.5px] font-medium leading-snug text-ink">{t.title}</span>
          <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-[12.5px] text-ink-muted">
            <span className="break-words">{t.institution.name ?? "—"}</span>
            {t.institution.confirmation === "machine_proposed" ? <Badge tone="warn" glyph={false}>por confirmar</Badge> : null}
          </span>
        </span>
        <span className="text-[13px] text-ink">
          <span className="block">{closingWhen(t.closes_at)}</span>
          <span className={`flex items-center gap-1.5 text-[11px] ${urgent ? "font-medium text-bad" : "text-ink-muted"}`}>
            {urgent ? <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-bad" /> : null}
            {closingLabel(t.closes_at, now)}
          </span>
        </span>
        {bidderReady === undefined ? <span className="text-[12px] text-ink-muted">—</span> : <Progress tender={t} bidderReady={bidderReady} />}
        <span className="text-[13px] tabular-nums text-ink">
          {amount ?? "—"}
          {amount && t.amount?.source === "estimado" ? <span className="ml-1 text-[11px] text-ink-muted">estimado</span> : null}
        </span>
        <span>
          <Badge tone={STATUS_TONE[t.status]}>{STATUS_LABEL[t.status]}</Badge>
        </span>
      </a>
    </li>
  );
}
