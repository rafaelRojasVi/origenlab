import { useEffect, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import type { CrmSection, DatosTab } from "../crmRoute";
import { useMayRunCaseCommands } from "../caseCommands";
import { inSharedMailbox } from "../gmailLinks";
import { undoMailRuleAction, refusalMessage } from "../mailRules";
import {
  RULE_GUIDE,
  actionSentence,
  fetchOperations,
  type OperationsPayload,
  type OpsAction,
  type OpsCard,
  type OpsStatus,
  type SentQuoteUnrecordedOnCase,
  type SentQuoteWithoutCase,
} from "../operations";
import { historicalCount, organizationsToConfirm } from "../today";
import { Badge, Button, EmptyState, ExternalLink, PageHeader, Panel, ResourceGate, Segmented, Skeleton, fmtDateTime } from "../ui";
import { useResource } from "../useResource";
import { fetchPipeline } from "../crmApi";
import { fetchPersonSuggestions } from "../authoring/crmAuthoringApi";
import { CompleteSentQuote } from "./CompleteSentQuote";
import { DatosBody, TECHNICAL_TABS } from "./DatosPage";
import { DrivePage } from "./DrivePage";
import { ProvidersPage } from "./ProvidersPage";
import { TriagePanel } from "./TriagePanel";

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
  if (tab === "decidir") return { view: "decidir", more: "proveedores", technical: "bloqueos" };
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
              <Rules actions={data.actions} />
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

function Rules({ actions }: { actions: OpsAction[] }) {
  const todayCount = (id: string) => actions.filter((a) => a.rule_id === id && a.today).length;
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
    <div className="space-y-4">
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
        <SentQuotesWithoutCase items={data.sent_quotes_without_case ?? []} navigate={navigate} />
        <SentQuotesUnrecordedOnCase items={data.sent_quotes_unrecorded_on_case ?? []} navigate={navigate} />
      </Panel>
      {/* «Por ordenar»: the housekeeping panels moved from Hoy (2026-10-15 redesign). */}
      <PorOrdenarSection navigate={navigate} />
    </div>
  );
}

/**
 * «Por ordenar» (2026-10-15): housekeeping panels moved from «Hoy» to keep the operator's daily
 * queue focused. Fetches the pipeline on demand (only when the «Por decidir» view is open).
 * Shows: emails without a case (TriagePanel), institutions to confirm, the full follow-up list,
 * people to add, historical cases and blocked cases.
 */
function PorOrdenarSection({ navigate }: { navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void }) {
  const [pipeline, reloadPipeline] = useResource(fetchPipeline);
  const [people] = useResource(fetchPersonSuggestions);
  const now = new Date();

  const items = pipeline.kind === "ready" ? pipeline.data.items : [];
  const orgs = organizationsToConfirm(items);
  const historical = historicalCount(items, now);
  const blocked = items.filter((c) => c.status === "blocked");
  const peopleCount = people.kind === "ready" ? people.data.total : 0;

  return (
    <div className="space-y-4">
      {/* Emails no case holds: the triage inbox. */}
      <TriagePanel cards={items} onCaseChanged={reloadPipeline} />
      {orgs.length > 0 ? (
        <Panel title="Instituciones por confirmar" aside={<Badge tone="warn" glyph={false}>{orgs.length}</Badge>} bodyClassName="divide-y divide-line">
          {orgs.map((o) => (
            <div key={o.organization_id} className="flex items-center gap-2 px-3 py-2 text-[13px]">
              <span className="flex-1 truncate font-medium text-ink">{o.name}</span>
              <span className="text-[11px] text-ink-muted">{o.cases.length} {o.cases.length === 1 ? "caso" : "casos"}</span>
              <Button onClick={() => navigate("organizaciones", o.organization_id)}>Ver</Button>
            </div>
          ))}
        </Panel>
      ) : null}
      {/* Seguimientos pendientes removed: duplicated in Hoy (Seguimientos section) and Oportunidades. */}
      {peopleCount > 0 ? (
        <Panel title="Personas por agregar" aside={<Badge tone="neutral" glyph={false}>{peopleCount}</Badge>}>
          <p className="px-3 py-2 text-[13px] text-ink-muted">
            {peopleCount} {peopleCount === 1 ? "persona" : "personas"} de correos de cotización aún no están en el CRM.
          </p>
          <div className="px-3 pb-2">
            <Button onClick={() => navigate("personas")}>Revisarlas en Personas</Button>
          </div>
        </Panel>
      ) : null}
      {historical > 0 ? (
        <Panel title="Casos por decidir" aside={<Badge tone="info" glyph={false}>{historical}</Badge>}>
          <p className="px-3 py-2 text-[13px] text-ink-muted">
            {historical} {historical === 1 ? "caso" : "casos"} del historial siguen «sin decidir»: ningún operador los ha marcado como vivos o cerrados.
          </p>
          <div className="px-3 pb-2">
            <Button variant="primary" onClick={() => navigate("oportunidades")}>Decidirlos en Oportunidades</Button>
          </div>
        </Panel>
      ) : null}
      {blocked.length > 0 ? (
        <Panel title="Casos bloqueados" aside={<Badge tone="bad" glyph={false}>{blocked.length}</Badge>} bodyClassName="divide-y divide-line">
          {blocked.map((c) => (
            <div key={c.opportunity_id} className="flex items-center gap-2 px-3 py-2 text-[13px]">
              <div className="flex-1 min-w-0">
                <button type="button" onClick={() => navigate("oportunidades", c.opportunity_id)}
                  className="truncate font-medium text-ink hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600">
                  {c.organization?.name ?? c.title}
                </button>
                <span className="block truncate text-[11px] text-bad">{c.attention.find((a) => a.blocking)?.label}</span>
              </div>
              <Button onClick={() => navigate("oportunidades", c.opportunity_id)}>Resolver</Button>
            </div>
          ))}
        </Panel>
      ) : null}
    </div>
  );
}

/**
 * Quotes contacto@ sent that no case holds (no rule could tell which case: a free-mail customer and
 * no institution the CRM knows in the PDF name). «Completar…» asks for the institution — prefilled
 * with what the PDF name says — and the API opens the case on that email, in «Enviada», with the
 * institution and the quote (`CompleteSentQuote`), then goes to it.
 */
function SentQuotesWithoutCase({ items, navigate }: {
  items: SentQuoteWithoutCase[];
  navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void;
}) {
  const mayOpen = useMayRunCaseCommands();
  const [open, setOpen] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <div className="border-t border-line">
      <h3 className="px-3 pt-2 text-[12px] font-semibold text-ink">Cotizaciones enviadas sin caso · {items.length}</h3>
      <p className="px-3 text-[11px] text-ink-muted">
        Ninguna regla pudo saber a qué caso pertenecen. «Completar…» crea el caso en «Enviada» con su institución y la
        cotización.
      </p>
      <ul className="divide-y divide-line text-[13px]">
        {items.map((q) => {
          const gmail = q.thread_id ? inSharedMailbox(`https://mail.google.com/mail/u/0/#all/${q.thread_id}`) : null;
          return (
            <li key={q.source_record_id} className="space-y-2 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="flex-1 tabular-nums">Cotización {q.quote_number} · enviada {fmtDateTime(q.sent_at)}</span>
                {gmail ? <ExternalLink href={gmail}>Gmail</ExternalLink> : null}
                {mayOpen ? (
                  <Button
                    variant={open === q.source_record_id ? "quiet" : "primary"}
                    aria-label={`Completar la cotización ${q.quote_number}`}
                    aria-expanded={open === q.source_record_id}
                    onClick={() => setOpen(open === q.source_record_id ? null : q.source_record_id)}
                  >
                    {open === q.source_record_id ? "Cerrar" : "Completar…"}
                  </Button>
                ) : null}
              </div>
              {open === q.source_record_id ? (
                <CompleteSentQuote
                  sourceRecordId={q.source_record_id}
                  quoteNumber={q.quote_number}
                  suggestion={q.suggested_institution}
                  onDone={(caseId) => navigate("oportunidades", caseId)}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Quotes contacto@ sent on an email the rules already linked to one open case, but whose number no
 * case records: a second quote on the same thread, linked before the rules learned to record it
 * (e.g. 4 balances quoted on the thread of an earlier 1-balance quote). «Registrar en el caso»
 * records it on that case with `CompleteSentQuote`, the case's institution prefilled.
 */
function SentQuotesUnrecordedOnCase({ items, navigate }: {
  items: SentQuoteUnrecordedOnCase[];
  navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void;
}) {
  const mayOpen = useMayRunCaseCommands();
  const [open, setOpen] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <div className="border-t border-line">
      <h3 className="px-3 pt-2 text-[12px] font-semibold text-ink">Cotizaciones enviadas en un caso, sin registrar · {items.length}</h3>
      <p className="px-3 text-[11px] text-ink-muted">
        El correo ya está en el caso, pero su número de cotización no quedó registrado. «Registrar en el caso» la agrega
        como cotización enviada.
      </p>
      <ul className="divide-y divide-line text-[13px]">
        {items.map((q) => {
          const gmail = q.thread_id ? inSharedMailbox(`https://mail.google.com/mail/u/0/#all/${q.thread_id}`) : null;
          return (
            <li key={q.source_record_id} className="space-y-2 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="flex-1">
                  <span className="tabular-nums">Cotización {q.quote_number}</span> · enviada {fmtDateTime(q.sent_at)} · en{" "}
                  <button type="button" className="text-brand-700 hover:underline" onClick={() => navigate("oportunidades", q.case_id)}>
                    {q.case_title}
                  </button>
                </span>
                {gmail ? <ExternalLink href={gmail}>Gmail</ExternalLink> : null}
                {mayOpen ? (
                  <Button
                    variant={open === q.source_record_id ? "quiet" : "primary"}
                    aria-label={`Registrar la cotización ${q.quote_number} en su caso`}
                    aria-expanded={open === q.source_record_id}
                    onClick={() => setOpen(open === q.source_record_id ? null : q.source_record_id)}
                  >
                    {open === q.source_record_id ? "Cerrar" : "Registrar en el caso"}
                  </Button>
                ) : null}
              </div>
              {open === q.source_record_id ? (
                <CompleteSentQuote
                  sourceRecordId={q.source_record_id}
                  quoteNumber={q.quote_number}
                  suggestion={q.organization_name}
                  opportunity={{ id: q.case_id, version: q.case_version }}
                  onDone={(caseId) => navigate("oportunidades", caseId)}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
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
