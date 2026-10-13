import { useEffect, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import type { CrmSection, DatosTab } from "../crmRoute";
import { undoMailRuleAction, refusalMessage } from "../mailRules";
import {
  RULE_GUIDE,
  actionSentence,
  fetchOperations,
  type OperationsPayload,
  type OpsAction,
  type OpsCard,
  type OpsStatus,
} from "../operations";
import { Badge, Button, EmptyState, ExternalLink, PageHeader, Panel, ResourceGate, Segmented, Skeleton, fmtDateTime } from "../ui";
import { useResource } from "../useResource";
import { DatosBody, TECHNICAL_TABS } from "./DatosPage";
import { DrivePage } from "./DrivePage";
import { ProvidersPage } from "./ProvidersPage";

type View = "general" | "actividad" | "reglas" | "decidir" | "mas";
type More = "proveedores" | "drive" | "tecnico";

const VIEW_LABEL: Record<View, string> = {
  general: "Estado",
  actividad: "Lo que hizo el sistema",
  reglas: "Reglas",
  decidir: "Por decidir",
  mas: "Más",
};

const STATUS: Record<OpsStatus, { word: string; dot: string; tone: "good" | "warn" | "bad" | "info" }> = {
  ok: { word: "OK", dot: "bg-good", tone: "good" },
  warn: { word: "Atención", dot: "bg-warn", tone: "warn" },
  bad: { word: "Falla", dot: "bg-bad", tone: "bad" },
  info: { word: "Info", dot: "bg-info", tone: "info" },
};

const REFRESH_MS = 60_000;

/** Where an old «Datos» link (`#/crm/datos/<tab>`) lands now. */
function landing(tab: DatosTab | undefined): { view: View; more: More; technical: DatosTab } {
  if (tab === "proveedores" || tab === "drive") return { view: "mas", more: tab, technical: "bloqueos" };
  if (tab && (TECHNICAL_TABS as readonly string[]).includes(tab)) return { view: "mas", more: "tecnico", technical: tab };
  return { view: "general", more: "proveedores", technical: "bloqueos" };
}

/**
 * «Operaciones» (spec 2026-10-13): the state of each part of the system, as Render shows its
 * services; what the email rules did; the rules in Spanish; what waits for a person. Everyone sees
 * it; the buttons that change something stay with an admin.
 */
export function OperacionesPage({ navigate, tab }: { navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void; tab?: DatosTab }) {
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";
  const start = landing(tab);
  const [view, setView] = useState<View>(start.view);
  const [more, setMore] = useState<More>(start.more);
  const [state, reload] = useResource(fetchOperations);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  return (
    <div className="space-y-3">
      <PageHeader
        title="Operaciones"
        subtitle="Cómo está cada parte del sistema y qué hizo solo."
        actions={<Button onClick={reload}>Actualizar</Button>}
      />
      <Segmented
        label="Sección"
        value={view}
        onChange={setView}
        options={(Object.keys(VIEW_LABEL) as View[]).map((value) => ({ value, label: VIEW_LABEL[value] }))}
      />
      {view === "mas" ? (
        <MoreView more={more} setMore={setMore} isAdmin={isAdmin} technical={start.technical} navigate={navigate} />
      ) : (
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton cards rows={4} />}>
          {(data) =>
            view === "general" ? (
              <StatusGrid cards={data.cards} generatedAt={data.generated_at} />
            ) : view === "actividad" ? (
              <Activity actions={data.actions} isAdmin={isAdmin} reload={reload} />
            ) : view === "reglas" ? (
              <Rules actions={data.actions} generatedAt={data.generated_at} />
            ) : (
              <ToDecide data={data} navigate={navigate} isAdmin={isAdmin} goTo={setView} />
            )
          }
        </ResourceGate>
      )}
    </div>
  );
}

function StatusGrid({ cards, generatedAt }: { cards: OpsCard[]; generatedAt: string }) {
  const worst = cards.some((c) => c.status === "bad") ? "bad" : cards.some((c) => c.status === "warn") ? "warn" : "ok";
  return (
    <div className="space-y-2">
      <p className="text-xs text-ink-muted" aria-live="polite">
        {worst === "ok" ? "Todo funciona." : worst === "warn" ? "Hay algo que mirar." : "Algo está fallando."}{" "}
        <span className="text-ink-faint">Medido {fmtDateTime(generatedAt)}.</span>
      </p>
      <ul className="grid gap-2 md:grid-cols-2">
        {cards.map((c) => (
          <li key={c.key}>
            <StatusCard card={c} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusCard({ card }: { card: OpsCard }) {
  const [open, setOpen] = useState(false);
  const s = STATUS[card.status];
  const links = card.facts.filter((f) => typeof f.value === "string" && String(f.value).startsWith("https://"));
  const numbers = card.facts.filter((f) => !links.includes(f));
  return (
    <section
      data-testid={`ops-card-${card.key}`}
      className="h-full rounded-lg border border-line bg-canvas-raised px-3 py-2.5 transition-colors duration-200 motion-reduce:transition-none"
    >
      <header className="flex items-center gap-2">
        <span aria-hidden className={`size-2 shrink-0 rounded-full ${s.dot}`} />
        <h2 className="min-w-0 truncate text-[13px] font-semibold text-ink">{card.title}</h2>
        <Badge tone={s.tone}>{s.word}</Badge>
        {card.at ? <span className="ml-auto text-[11px] tabular-nums text-ink-faint">{fmtDateTime(card.at)}</span> : null}
      </header>
      <p className="mt-1 text-[13px] text-ink">{card.summary}</p>
      {numbers.length ? (
        <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-ink-muted">
          {numbers.map((f) => (
            <div key={f.label} className="flex gap-1">
              <dt>{f.label}</dt>
              <dd className="font-medium tabular-nums text-ink">{f.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {links.map((f) => (
        <p key={f.label} className="mt-1 text-[11px]">
          <ExternalLink href={String(f.value)}>{`Ver en ${f.label}`}</ExternalLink>
        </p>
      ))}
      {card.events.length ? (
        <div className="mt-1.5">
          <button
            type="button"
            aria-expanded={open}
            aria-label={`Ver actividad de ${card.title}`}
            onClick={() => setOpen((v) => !v)}
            className="rounded-sm text-[11px] font-medium text-brand-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
          >
            {open ? "Ocultar actividad" : "Ver actividad"}
          </button>
          {open ? (
            <ol className="mt-1 space-y-0.5 border-l border-line pl-2 text-[11px] text-ink-muted">
              {card.events.map((e, i) => (
                <li key={`${e.at}-${i}`} className="flex gap-2">
                  <span className="tabular-nums text-ink-faint">{fmtDateTime(e.at)}</span>
                  <span>{e.text}</span>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Activity({ actions, isAdmin, reload }: { actions: OpsAction[]; isAdmin: boolean; reload: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!actions.length) return <EmptyState title="Nada en los últimos 7 días">Las reglas no hicieron ningún cambio.</EmptyState>;
  async function undo(a: OpsAction) {
    const note = window.prompt("¿Por qué se deshace? (queda en el historial)");
    if (!note?.trim()) return;
    setBusy(a.receipt_id);
    setError(null);
    try {
      await undoMailRuleAction(a.receipt_id, note.trim());
      reload();
    } catch (err) {
      setError(refusalMessage(err, "No se pudo deshacer"));
    } finally {
      setBusy(null);
    }
  }
  return (
    <Panel title="Lo que hizo el sistema" note="últimos 7 días">
      {error ? <p role="alert" className="px-3 pt-2 text-xs text-bad">{error}</p> : null}
      <ul className="divide-y divide-line">
        {actions.map((a) => (
          <li key={a.receipt_id} className="flex flex-wrap items-start gap-2 px-3 py-2">
            <div className="min-w-0 flex-1">
              <p className={`text-[13px] ${a.undone ? "text-ink-faint line-through" : "text-ink"}`}>{actionSentence(a)}</p>
              <p className="text-[11px] text-ink-faint">
                {fmtDateTime(a.at)} · {a.automatic ? "automático" : "aplicado por una persona"}
              </p>
            </div>
            {!a.undone ? (
              <Button
                disabled={!isAdmin}
                title={isAdmin ? undefined : "Solo administración"}
                busy={busy === a.receipt_id}
                busyLabel="Deshaciendo…"
                onClick={() => void undo(a)}
              >
                Deshacer
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Rules({ actions, generatedAt }: { actions: OpsAction[]; generatedAt: string }) {
  const today = generatedAt.slice(0, 10);
  const todayCount = (id: string) => actions.filter((a) => a.rule_id === id && (a.at ?? "").startsWith(today)).length;
  return (
    <div className="space-y-2">
      <p className="text-xs text-ink-muted">
        Cada correo capturado pasa por estas reglas. Las automáticas actúan solas cada 5 minutos; las demás esperan a
        que una persona pulse «Aplicar». Todo se puede deshacer.
      </p>
      <ul className="grid gap-2 md:grid-cols-2">
        {Object.entries(RULE_GUIDE).map(([id, g]) => (
          <li key={id} data-testid={`rule-${id}`} className="rounded-lg border border-line bg-canvas-raised px-3 py-2.5">
            <header className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[11px] text-ink-faint">{id}</span>
              <Badge tone={g.automatica ? "good" : "neutral"}>{g.automatica ? "Automática" : "Con «Aplicar»"}</Badge>
              <span className="ml-auto text-[11px] tabular-nums text-ink-faint">hoy: {todayCount(id)}</span>
            </header>
            <p className="mt-1 text-[13px] text-ink"><strong className="font-semibold">Cuándo:</strong> {g.cuando}</p>
            <p className="mt-0.5 text-[13px] text-ink"><strong className="font-semibold">Qué hace:</strong> {g.hace}</p>
            <p className="mt-0.5 text-[11px] text-ink-muted">Ejemplo: {g.ejemplo}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ToDecide({
  data,
  navigate,
  isAdmin,
  goTo,
}: {
  data: OperationsPayload;
  navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void;
  isAdmin: boolean;
  goTo: (v: View) => void;
}) {
  const { cases_without_institution: noInstitution, institutions_por_confirmar: porConfirmar } = data.counts;
  return (
    <Panel title="Por decidir" note="lo que espera a una persona">
      <ul className="divide-y divide-line text-[13px]">
        <li className="flex flex-wrap items-center gap-2 px-3 py-2">
          <span className="flex-1">{noInstitution} casos sin institución</span>
          <Button onClick={() => navigate("oportunidades")}>Ver casos</Button>
        </li>
        <li className="flex flex-wrap items-center gap-2 px-3 py-2">
          <span className="flex-1">{porConfirmar} instituciones «por confirmar»</span>
          <Button onClick={() => navigate("organizaciones")}>Ver instituciones</Button>
        </li>
        <li className="flex flex-wrap items-center gap-2 px-3 py-2">
          <span className="flex-1">Propuestas de las reglas del correo (vista previa y «Aplicar»)</span>
          <Button disabled={!isAdmin} title={isAdmin ? undefined : "Solo administración"} onClick={() => goTo("mas")}>
            Abrir
          </Button>
        </li>
      </ul>
      <p className="px-3 pb-2 text-[11px] text-ink-faint">Pronto: una sola lista con «Aceptar» y «Descartar» para cada decisión.</p>
    </Panel>
  );
}

function MoreView({
  more,
  setMore,
  isAdmin,
  technical,
  navigate,
}: {
  more: More;
  setMore: (m: More) => void;
  isAdmin: boolean;
  technical: DatosTab;
  navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void;
}) {
  const options: { value: More; label: string }[] = [
    { value: "proveedores", label: "Proveedores" },
    { value: "drive", label: "Archivo Drive" },
    ...(isAdmin ? [{ value: "tecnico" as More, label: "Técnico" }] : []),
  ];
  const current = more === "tecnico" && !isAdmin ? "proveedores" : more;
  return (
    <div className="space-y-3">
      <Segmented label="Más" value={current} onChange={setMore} options={options} />
      {current === "proveedores" ? (
        <ProvidersPage />
      ) : current === "drive" ? (
        <DrivePage navigate={navigate} />
      ) : (
        <DatosBody navigate={navigate} initialTab={technical} embedded />
      )}
    </div>
  );
}
