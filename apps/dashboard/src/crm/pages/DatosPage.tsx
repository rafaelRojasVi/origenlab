import { useMemo, useState } from "react";
import { fetchReview, fetchWorkQueue } from "../crmApi";
import type { ReviewResponse, WorkQueueItem } from "../crmTypes";
import type { CrmSection, DatosTab } from "../crmRoute";
import {
  Badge,
  EmptyState,
  ExternalLink,
  LocalDriveLink,
  PageHeader,
  Panel,
  ResourceGate,
  Segmented,
  Skeleton,
  StatLine,
  fmtInt,
} from "../ui";
import { useAuthSession } from "../../context/AuthSessionContext";
import { useResource } from "../useResource";
import { DataHealth } from "./DataHealth";
import { MailRulesPanel } from "./MailRulesPanel";
import { ProvidersPage } from "./ProvidersPage";
import { DrivePage } from "./DrivePage";

const QUEUE_LABEL: Record<string, { label: string; action: string; blocking: boolean }> = {
  canonical_undetermined: { label: "Revisión canónica indeterminada", action: "Anular o reemplazar la revisión duplicada", blocking: true },
  shared_printed_number: { label: "Número impreso compartido", action: "Decidir a qué caso pertenece cada documento", blocking: true },
  case_without_institution: { label: "Caso sin institución", action: "Confirmar la institución solicitante", blocking: true },
  case_without_quote: { label: "Caso sin cotización enviada", action: "Registrar la cotización", blocking: false },
  unresolved_document: { label: "Documento sin resolver", action: "Resolver o rechazar la referencia", blocking: false },
  pending_evidence: { label: "Evidencia pendiente de revisión", action: "Revisar el registro", blocking: false },
};

const ASSERTION_LABEL: Record<string, string> = {
  supplier_candidate: "Candidatos de proveedor",
  organization_name: "Nombres de institución",
  contact_address: "Direcciones de contacto",
  document_reference: "Referencias a documentos",
};

const LEDGER_STATUS: Record<string, { label: string; tone: "warn" | "bad" | "neutral" }> = {
  held: { label: "Retenido por el owner", tone: "bad" },
  pending_organization_confirmation: { label: "Falta confirmar institución", tone: "warn" },
};

/** The tab badge counts what blocks the CRM. Pending evidence is the Gmail inflow's pile — it can
 *  run to hundreds a week and would drown the blockers — so it is shown inside the tab, not here.
 *  `counts` covers the whole queue, not just the page; without it only the page's items are known. */
export function blockingCount(queue: { items: WorkQueueItem[]; counts?: Record<string, number> }): number {
  if (queue.counts) {
    return Object.entries(queue.counts).reduce((n, [kind, c]) => (kind === "pending_evidence" ? n : n + c), 0);
  }
  return queue.items.filter((i) => i.kind !== "pending_evidence").length;
}

const TAB_LABEL: Record<DatosTab, string> = {
  bloqueos: "Bloqueos técnicos",
  no_importadas: "No importadas",
  evidencia: "Evidencia",
  estado: "Estado de los datos",
  acciones: "Acciones automáticas",
  proveedores: "Proveedores",
  drive: "Archivo Drive",
};

/**
 * «Datos» (admin only): how the system is doing and its technical switches — the blockers that
 * are not a sales decision, archived quotations not imported, open evidence, data health, the
 * automatic mail actions, suppliers and the Drive archive. Anyone else sees one line and nothing
 * is read on their behalf.
 */
export function DatosPage({ navigate, tab }: { navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void; tab?: DatosTab }) {
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";
  if (!isAdmin) {
    return (
      <div className="space-y-3">
        <PageHeader title="Datos" />
        <p className="text-sm text-ink-muted">Sólo administración</p>
      </div>
    );
  }
  return <DatosBody navigate={navigate} initialTab={tab ?? "bloqueos"} />;
}

function DatosBody({ navigate, initialTab }: { navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void; initialTab: DatosTab }) {
  const [queue, reloadQueue] = useResource(fetchWorkQueue);
  const [review, reloadReview] = useResource(fetchReview);
  const [tab, setTab] = useState<DatosTab>(initialTab);
  return (
    <div className="space-y-3">
      <PageHeader title="Datos" subtitle="Estado del sistema y ajustes técnicos. Sólo administración." />
      <Segmented
        label="Sección"
        value={tab}
        onChange={setTab}
        options={(Object.keys(TAB_LABEL) as DatosTab[]).map((value) => ({
          value,
          label: TAB_LABEL[value],
          count:
            value === "bloqueos" && queue.kind === "ready"
              ? blockingCount(queue.data)
              : value === "no_importadas" && review.kind === "ready"
                ? review.data.archived_not_in_crm.length
                : undefined,
        }))}
      />
      {tab === "acciones" ? (
        <MailRulesPanel />
      ) : tab === "estado" ? (
        <DataHealth navigate={navigate} />
      ) : tab === "proveedores" ? (
        <ProvidersPage />
      ) : tab === "drive" ? (
        <DrivePage navigate={navigate} />
      ) : tab === "bloqueos" ? (
        <ResourceGate state={queue} reload={reloadQueue} skeleton={<Skeleton rows={5} />}>
          {(q) => <CrmQueue items={q.items} counts={q.counts} navigate={navigate} />}
        </ResourceGate>
      ) : (
        <ResourceGate state={review} reload={reloadReview} skeleton={<Skeleton rows={5} />}>
          {(r) => (tab === "no_importadas" ? <NotImported review={r} /> : <Evidence review={r} />)}
        </ResourceGate>
      )}
    </div>
  );
}

function CrmQueue({
  items,
  counts,
  navigate,
}: {
  items: WorkQueueItem[];
  counts?: Record<string, number>;
  navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (items.length === 0) return <EmptyState title="Sin bloqueos técnicos">Ningún caso ni cotización necesita una decisión.</EmptyState>;
  const meta = (kind: string, fallback: string) => QUEUE_LABEL[kind] ?? { label: kind, action: fallback, blocking: false };
  // Blockers one by one; everything else grouped by kind, so three decisions are not buried
  // under a hundred routine review rows.
  const blocking = items.filter((i) => meta(i.kind, i.next_action).blocking);
  const grouped = new Map<string, WorkQueueItem[]>();
  for (const i of items) {
    if (meta(i.kind, i.next_action).blocking) continue;
    grouped.set(i.kind, [...(grouped.get(i.kind) ?? []), i]);
  }
  return (
    <div className="space-y-3">
      <Panel
        title="Requieren una decisión"
        note="Se calculan de nuevo cada vez que abres esta página"
        aside={<Badge tone={blocking.length ? "bad" : "good"}>{blocking.length}</Badge>}
        bodyClassName="divide-y divide-line"
      >
        {blocking.length === 0 ? (
          <p className="px-3 py-3 text-xs text-ink-muted">Ningún bloqueo.</p>
        ) : (
          blocking.map((item, i) => <QueueRow key={`${item.kind}-${i}`} item={item} m={meta(item.kind, item.next_action)} navigate={navigate} />)
        )}
      </Panel>
      <Panel title="Pendientes de rutina" bodyClassName="divide-y divide-line">
        {[...grouped.entries()].map(([kind, rows]) => {
          const m = meta(kind, rows[0].next_action);
          const labels = new Map<string, number>();
          for (const r of rows) labels.set(r.label ?? "—", (labels.get(r.label ?? "—") ?? 0) + 1);
          const open = expanded === kind;
          return (
            <div key={kind}>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setExpanded(open ? null : kind)}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5 text-left hover:bg-canvas-sunken/40"
              >
                <Badge tone="warn">Pendiente</Badge>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-medium text-ink">
                    {m.label} <span className="tabular-nums text-ink-muted">· {fmtInt(counts?.[kind] ?? rows.length)}</span>
                    {(counts?.[kind] ?? rows.length) > rows.length ? (
                      <span className="text-[11px] font-normal text-ink-faint"> · se muestran los {rows.length} más antiguos</span>
                    ) : null}
                  </span>
                  <span className="block text-[11px] text-ink-muted">
                    {[...labels.keys()].every((l) => l.length <= 24)
                      ? `${[...labels.entries()].map(([l, n]) => `${n} ${l}`).join(" · ")} — `
                      : ""}
                    siguiente: {m.action}
                  </span>
                </span>
                <span aria-hidden="true" className={`text-[10px] text-ink-faint transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
              </button>
              {open ? (
                <div className="divide-y divide-line/70 border-t border-line/70 bg-canvas-sunken/40">
                  {rows.map((item, i) => <QueueRow key={i} item={item} m={m} navigate={navigate} />)}
                </div>
              ) : null}
            </div>
          );
        })}
      </Panel>
    </div>
  );
}

function QueueRow({
  item,
  m,
  navigate,
}: {
  item: WorkQueueItem;
  m: { label: string; action: string; blocking: boolean };
  navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void;
}) {
  const oppId = item.subject_ids.opportunity_id;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5">
      <Badge tone={m.blocking ? "bad" : "warn"}>{m.blocking ? "Bloquea" : "Pendiente"}</Badge>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium text-ink">
          {m.label}
          {item.label ? <span className="font-normal text-ink-muted"> · {item.label}</span> : null}
        </p>
        <p className="text-[11px] text-ink-muted">Siguiente: {m.action}</p>
      </div>
      {item.age_days !== null ? <span className="text-[11px] tabular-nums text-ink-faint">{item.age_days} d</span> : null}
      {oppId ? (
        <button
          type="button"
          onClick={() => navigate("oportunidades", oppId)}
          className="h-7 rounded-md border border-line px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken"
        >
          Ver caso
        </button>
      ) : null}
    </div>
  );
}

function NotImported({ review }: { review: ReviewResponse }) {
  const [status, setStatus] = useState<string>("all");
  const groups = useMemo(() => {
    const m: Record<string, number> = {};
    for (const d of review.archived_not_in_crm) m[d.ledger_crm_status ?? "unknown"] = (m[d.ledger_crm_status ?? "unknown"] ?? 0) + 1;
    return m;
  }, [review.archived_not_in_crm]);
  if (!review.drive_configured) {
    return <EmptyState title="Registros del archivo no cargados">Sin ellos no se puede saber qué cotizaciones archivadas faltan en el CRM.</EmptyState>;
  }
  const rows = review.archived_not_in_crm.filter((d) => status === "all" || (d.ledger_crm_status ?? "unknown") === status);
  return (
    <>
      <StatLine
        items={Object.entries(groups).map(([k, n]) => ({
          label: LEDGER_STATUS[k]?.label ?? "Sin estado en el registro",
          value: n,
        }))}
      />
      <Segmented
        label="Motivo"
        value={status}
        onChange={setStatus}
        options={[{ value: "all", label: "Todas" }, ...Object.keys(groups).map((k) => ({ value: k, label: LEDGER_STATUS[k]?.label ?? "Sin estado" }))]}
      />
      <Panel
        title="Archivo histórico de Drive pendiente de revisión"
        note="Estos PDF ya están archivados. Son una cola histórica independiente de las nuevas cotizaciones registradas desde Gmail; el motivo es la decisión documentada al archivar."
        bodyClassName="divide-y divide-line"
      >
        {rows.map((d) => {
          const st = LEDGER_STATUS[d.ledger_crm_status ?? ""];
          return (
            <div key={d.document_sha256} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
              <span className="w-24 shrink-0 font-semibold tabular-nums text-ink">
                {d.quote_number ?? "—"}
                {d.revision ? <span className="font-normal text-ink-faint"> r{d.revision}</span> : null}
              </span>
              <span className="min-w-0 flex-1 truncate text-ink-muted" title={d.original_filename ?? undefined}>
                {d.original_filename ?? d.document_sha256.slice(0, 16)}
              </span>
              <Badge tone={st?.tone ?? "neutral"}>{st?.label ?? "Sin estado en el registro"}</Badge>
              <LocalDriveLink href={d.file_url}>PDF</LocalDriveLink>
              {d.gmail_url ? <ExternalLink href={d.gmail_url}>Gmail</ExternalLink> : null}
            </div>
          );
        })}
      </Panel>
      <p className="text-xs text-ink-muted">
        La importación masiva histórica no está habilitada en esta pantalla: los registros
        retenidos por el owner y los que no tienen institución confirmada requieren revisión
        individual y controles contra duplicados. Las cotizaciones nuevas enviadas por Gmail
        se procesan en «Acciones automáticas»; no se importan aquí.
      </p>
    </>
  );
}

function Evidence({ review }: { review: ReviewResponse }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {review.open_assertions.map((a) => (
          <div key={`${a.kind}:${a.resolution}`} className="rounded-lg border border-line bg-canvas-raised px-3.5 py-3">
            <p className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{ASSERTION_LABEL[a.kind] ?? a.kind}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{fmtInt(a.count)}</p>
            <Badge tone={a.resolution === "ambiguous" ? "bad" : "warn"}>{a.resolution === "ambiguous" ? "Ambiguas" : "Sin resolver"}</Badge>
          </div>
        ))}
      </div>
      <Panel title="Nombres de institución ambiguos" bodyClassName="divide-y divide-line">
        {review.ambiguous_organizations.length === 0 ? (
          <p className="px-3 py-3 text-xs text-ink-muted">Ninguno.</p>
        ) : (
          review.ambiguous_organizations.map((a) => (
            <div key={a.assertion_id} className="px-3 py-2">
              <p className="text-[13px] font-medium text-ink">{a.value_norm}</p>
              {a.ambiguity_note ? <p className="text-[11px] text-ink-muted">{a.ambiguity_note}</p> : null}
            </div>
          ))
        )}
      </Panel>
      <p className="text-[11px] text-ink-faint">
        La revisión detallada de evidencia está en la pantalla «Revisión de evidencia» del panel anterior (<a className="underline" href="#/revision">#/revision</a>).
      </p>
    </>
  );
}
