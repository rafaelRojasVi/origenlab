import { useCallback, useRef, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import { crmHash, noticeIdOfRoute, type CrmSection } from "../crmRoute";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, PageHeader, ResourceGate, Segmented, Skeleton, type Tone } from "../ui";
import { useResource } from "../useResource";
import { DiscoveredTenderPage } from "./DiscoveredTenderPage";
import { DiscoveredTenders } from "./DiscoveredTenders";
import { NewTenderDialog } from "./NewTenderDialog";
import { TenderPage } from "./TenderPage";
import { TENDER_STATUSES, fetchTenders, type TenderList, type TenderRow, type TenderStatus } from "./tenderApi";
import { closingLabel, closingWhen } from "./tenderTime";

/**
 * «Licitaciones» opens on «En Mercado Público» (`DiscoveredTenders`): the open tenders that fit the
 * catalog, each with «Preparar». «Mis licitaciones» is the tenders being prepared, as a list a salesperson reads at a glance — code and
 * title, the institution (marked «por confirmar» when the machine proposed it), when it closes and how
 * long is left (Santiago time), the amount (neto; «estimado» when it comes from the notice) and the
 * estado. Filter chips carry the count per estado; a row opens the tender's page (`TenderPage`). «Nueva licitación»
 * (sales and admin) opens one from a Mercado Público code or link. Every figure is the API's.
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

/** The grid shared by the header and the rows from `md` up. */
const GRID = "md:grid md:grid-cols-[minmax(0,2.2fr)_minmax(0,1.4fr)_minmax(0,1.2fr)_minmax(0,1fr)_7rem] md:items-center md:gap-4";

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
          { value: "mias", label: "Mis licitaciones" },
        ]}
      />
      {view === "mercado" ? (
        <DiscoveredTenders navigate={navigate} now={at} mayOpen={mayOpen} onPrepare={setAsking} />
      ) : (
        <MyTenders navigate={navigate} now={at} />
      )}
      {asking !== null ? (
        <NewTenderDialog navigate={navigate} initialReference={asking} onClose={() => setAsking(null)} />
      ) : null}
    </div>
  );
}

function MyTenders({ navigate, now: at }: { navigate: Navigate; now: Date }) {
  const [status, setStatus] = useState<TenderStatus | null>(null);
  const load = useCallback(() => fetchTenders(status ?? undefined), [status]);
  const [state, reload] = useResource(load, [status]);
  // The counts stay on the chips while another estado loads.
  const counts = useRef<TenderList["counts"]>(ZERO);
  if (state.kind === "ready") counts.current = state.data.counts;
  const total = TENDER_STATUSES.reduce((n, s) => n + counts.current[s], 0);

  return (
    <div className="space-y-4">
      <div role="group" aria-label="Filtrar por estado" className="flex flex-wrap gap-1.5">
        {TENDER_STATUSES.map((s) => (
          <Chip key={s} label={STATUS_LABEL[s]} count={counts.current[s]} pressed={status === s} onClick={() => setStatus(s)} />
        ))}
        <Chip label="Todas" count={total} pressed={status === null} onClick={() => setStatus(null)} />
      </div>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(data) =>
          data.tenders.length === 0 ? (
            <EmptyState title={status === null ? "Aún no hay licitaciones. Abre una con su código de Mercado Público." : EMPTY_FILTERED} />
          ) : (
            <section aria-label="Licitaciones" className="overflow-hidden rounded-xl border border-line bg-canvas-raised">
              <div aria-hidden="true" className={`hidden border-b border-line bg-canvas-sunken px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-ink-faint ${GRID}`}>
                <span>Licitación</span>
                <span>Institución</span>
                <span>Cierre</span>
                <span>Monto</span>
                <span>Estado</span>
              </div>
              <ul className="divide-y divide-line">
                {data.tenders.map((t) => (
                  <TenderItem key={t.id} tender={t} now={at} navigate={navigate} />
                ))}
              </ul>
            </section>
          )
        }
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

function TenderItem({ tender: t, now, navigate }: { tender: TenderRow; now: Date; navigate: Navigate }) {
  const amount = t.amount ? formatMoney(String(t.amount.neto), t.amount.currency) : null;
  return (
    <li data-testid={`tender-row-${t.id}`}>
      <a
        href={crmHash("licitaciones", t.id)}
        onClick={(e) => {
          e.preventDefault();
          navigate("licitaciones", t.id);
        }}
        className={`flex min-w-0 flex-col gap-1 px-4 py-3 hover:bg-canvas-sunken/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${GRID}`}
      >
        <span className="min-w-0">
          {t.code ? <span className="block text-[11px] tabular-nums text-ink-muted">{t.code}</span> : null}
          <span className="block break-words text-[13px] font-medium text-ink">{t.title}</span>
        </span>
        <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-[13px] text-ink">
          <span className="break-words">{t.institution.name ?? "—"}</span>
          {t.institution.confirmation === "machine_proposed" ? <Badge tone="warn" glyph={false}>por confirmar</Badge> : null}
        </span>
        <span className="text-[13px] text-ink">
          <span className="block">{closingWhen(t.closes_at)}</span>
          <span className="block text-[11px] text-ink-muted">{closingLabel(t.closes_at, now)}</span>
        </span>
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
