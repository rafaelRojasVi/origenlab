import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { commandMissing } from "../../api/refusalEnvelope";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import { crmHash, type CrmSection } from "../crmRoute";
import { formatMoney } from "../money";
import { toCotizador } from "../quotes/openOrCreateQuote";
import { Badge, Button, ExternalLink, Section, Skeleton, buttonClass, fmtDateTime, toast } from "../ui";
import { AnnexUpload } from "./AnnexUpload";
import { ChecklistList, ResumenList } from "./TenderReading";
import { FilledForms, PackageBlock, SpecSheets } from "./TenderOutputs";
import { TenderStatusControl } from "./TenderStatusControl";
import {
  createTenderOfferQuote,
  newTenderCommandKey,
  prepareTenderBid,
  tenderRefusal,
  type Preparation,
  type PreparationPhase,
  type TenderPageData,
} from "./tenderApi";
import { MATCH_ES, QUOTE_STATUS_ES, capText, definitive, failureText, labelOf, refusalText } from "./tenderLabels";
import { closingLabel, closingWhen } from "./tenderTime";
import { POLL_MS, useTenderPage, type PollTrouble } from "./useTenderPage";

/**
 * One tender's page, in the order the work is done: upload the anexos, let the machine read them
 * («Preparar oferta» → Resumen and Checklist), create the economic offer in the Cotizador and approve
 * it there, let the machine fill the forms («Llenar formularios»), then download the package. Each
 * step says plainly what it needs when it cannot run yet. The page is read again every few seconds
 * only while the machine is working, and the line saying so is announced.
 *
 * Sales and admin only: a viewer sees one line and nothing is read (the API refuses them the page).
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

export const VIEWER_LINE = "Solo ventas y administración pueden ver esta licitación.";
const READING = "Leyendo los anexos… (unos minutos)";
const FILLING = "Llenando los formularios… (unos minutos)";
const BIDDER_MISSING = "Completa los Datos para licitaciones en Preferencias.";
const NEEDS_READ = "Primero lee los anexos con «Preparar oferta».";
const READ_RUNNING = "Espera a que termine la lectura.";
const RETRYING = "No se pudo actualizar; reintentando…";
const STOPPED = "No se pudo actualizar la licitación. Recarga la página.";

const running = (p: Preparation | null) => p?.status === "queued" || p?.status === "running";

export function TenderPage({
  id,
  navigate,
  now,
  pollMs,
}: {
  id: string;
  navigate: Navigate;
  /** A fixed clock, for tests. */
  now?: Date;
  /** The polling interval, for tests (default `POLL_MS`). */
  pollMs?: number;
}) {
  const { session } = useAuthSession();
  const allowed = session.kind === "signed_in" && ROLES_THAT_DECIDE_CASES.has(session.operator.role);
  const [state, reload, trouble] = useTenderPage(id, allowed, pollMs);

  if (!allowed) {
    return (
      <div className="space-y-3">
        <BackLink navigate={navigate} />
        <p role="status" className="rounded-lg border border-line bg-canvas-sunken/70 px-4 py-4 text-[13px] font-medium text-ink">
          {VIEWER_LINE}
        </p>
      </div>
    );
  }
  return (
    <div className="min-w-0 space-y-6">
      <BackLink navigate={navigate} />
      {state.kind === "loading" ? <Skeleton rows={6} /> : null}
      {state.kind === "error" ? <LoadProblem error={state.error} reload={reload} /> : null}
      {state.kind === "ready" ? <TenderBody data={state.data} reload={reload} trouble={trouble} announceMs={pollMs ?? POLL_MS} now={now ?? new Date()} /> : null}
    </div>
  );
}

function BackLink({ navigate }: { navigate: Navigate }) {
  return (
    <a
      href={crmHash("licitaciones")}
      onClick={(e) => {
        e.preventDefault();
        navigate("licitaciones");
      }}
      className="inline-flex min-h-[44px] items-center text-[13px] font-medium text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      ← Volver a Licitaciones
    </a>
  );
}

function LoadProblem({ error, reload }: { error: unknown; reload: () => void }) {
  const r = tenderRefusal(error);
  let text = "No se pudo leer la licitación.";
  if (r?.code === "tender_not_found") text = "No existe esa licitación.";
  else if (commandMissing(r)) text = "Esta página no está habilitada en este entorno.";
  else if (r?.status === 403) text = VIEWER_LINE;
  return (
    <div role="alert" className="rounded-lg border border-line bg-canvas-sunken/70 px-4 py-4">
      <p className="text-[13px] font-semibold text-ink">{text}</p>
      <Button className="mt-3 min-h-[44px] px-4 text-[13px]" onClick={reload}>
        Reintentar
      </Button>
    </div>
  );
}

/* ── preparing: one key per intended request, kept for a retry that may have run ── */

function usePrepare(tenderId: string, cap: string, onDone: () => void) {
  const keys = useRef<Partial<Record<PreparationPhase, { sig: string; key: string }>>>({});
  const [busy, setBusy] = useState<PreparationPhase | null>(null);
  const [problems, setProblems] = useState<Partial<Record<PreparationPhase, string>>>({});

  const run = async (phase: PreparationPhase, latestId: string | null) => {
    if (busy) return;
    const sig = JSON.stringify([tenderId, phase, latestId]);
    if (keys.current[phase]?.sig !== sig) keys.current[phase] = { sig, key: newTenderCommandKey() };
    setBusy(phase);
    setProblems((p) => ({ ...p, [phase]: undefined }));
    try {
      await prepareTenderBid(tenderId, phase, keys.current[phase]!.key);
      keys.current[phase] = undefined;
      onDone();
    } catch (err) {
      if (definitive(err)) keys.current[phase] = undefined;
      setProblems((p) => ({ ...p, [phase]: refusalText(err, cap) }));
      if (tenderRefusal(err)?.code === "preparation_running") onDone();
    } finally {
      setBusy(null);
    }
  };
  return { busy, problems, run };
}

/** A step's button: disabled with its reason (announced as its description), and its refusal below. */
function StepButton({
  label,
  ariaLabel,
  reason,
  busy,
  busyLabel,
  problem,
  variant = "primary",
  onClick,
}: {
  label: string;
  ariaLabel?: string;
  reason: string | null;
  busy: boolean;
  busyLabel: string;
  problem?: string;
  variant?: "primary" | "secondary";
  onClick: () => void;
}) {
  const reasonId = useId();
  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        variant={variant}
        className="min-h-[44px] px-4 text-[13px]"
        aria-label={ariaLabel}
        aria-describedby={reason ? reasonId : undefined}
        disabled={!!reason}
        busy={busy}
        busyLabel={busyLabel}
        onClick={onClick}
      >
        {label}
      </Button>
      {reason ? (
        <span id={reasonId} className="text-[12px] text-ink-muted">
          {reason}
        </span>
      ) : null}
      {problem ? (
        <span role="alert" className="text-[12.5px] text-bad">
          {problem}
        </span>
      ) : null}
    </div>
  );
}

function Card({ children }: { children: ReactNode }) {
  return <div className="min-w-0 space-y-3">{children}</div>;
}

function Muted({ children }: { children: ReactNode }) {
  return <p className="text-[13px] text-ink-muted">{children}</p>;
}

function Failure({ prep, cap }: { prep: Preparation; cap: string }) {
  return <p className="text-[13px] font-medium text-bad">{failureText(prep.failure_code, prep.status, cap)}</p>;
}

/* ── the page ── */

/** Once a phase the page saw working is done, the line to announce: done, or why it failed. */
function useCompletion(read: Preparation | null, fill: Preparation | null, cap: string, ms: number): string | null {
  const seen = useRef<Partial<Record<PreparationPhase, { id: string; busy: boolean }>>>({});
  const [line, setLine] = useState<string | null>(null);
  useEffect(() => {
    let said: string | null = null;
    for (const prep of [read, fill]) {
      if (!prep) continue;
      const before = seen.current[prep.phase];
      const busy = running(prep);
      if (before?.id === prep.id && before.busy && !busy) {
        said = prep.status === "ready" ? (prep.phase === "read" ? "Lectura lista." : "Formularios listos.") : failureText(prep.failure_code, prep.status, cap);
      }
      seen.current[prep.phase] = { id: prep.id, busy };
    }
    if (said === null) return undefined;
    setLine(said);
    const timer = setTimeout(() => setLine(null), ms);
    return () => clearTimeout(timer);
  }, [read, fill, cap, ms]);
  return line;
}

function TenderBody({
  data,
  reload,
  trouble,
  announceMs,
  now,
}: {
  data: TenderPageData;
  reload: () => void;
  trouble: PollTrouble;
  /** How long a «lista» line stays in the live region: one polling cycle. */
  announceMs: number;
  now: Date;
}) {
  const { tender, annexes, offer_quote: offer, spend, bidder_ready: bidderReady } = data;
  const read = data.preparations.read;
  const fill = data.preparations.fill;
  const cap = spend.cap_usd;
  const prepare = usePrepare(tender.id, cap, reload);
  const completion = useCompletion(read, fill, cap, announceMs);
  const offerKey = useRef<{ sig: string; key: string } | null>(null);
  const [offerBusy, setOfferBusy] = useState(false);
  const [offerProblem, setOfferProblem] = useState<string | null>(null);

  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of read?.result?.annexes ?? []) m.set(a.document_id, a.filename);
    for (const a of annexes) m.set(a.document_id, a.filename);
    return m;
  }, [annexes, read]);
  const productNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const item of read?.result?.items ?? []) {
      for (const c of item.candidatos ?? []) {
        const name = c.nombre || c.modelo;
        if (name) m.set(c.product_id, name);
      }
    }
    return m;
  }, [read]);

  const readReady = read?.status === "ready";
  const readRunning = running(read);
  const fillRunning = running(fill);
  const capReached = spend.reached;
  const readReason = annexes.length === 0 ? "Sube los anexos primero." : capReached ? capText(cap) : null;
  const offerApproved = offer?.status === "approved" || offer?.status === "sent";
  const fillReason = !bidderReady
    ? BIDDER_MISSING
    : readRunning
      ? READ_RUNNING
      : !readReady
        ? NEEDS_READ
        : !offer
          ? "Crea la oferta económica primero."
          : !offerApproved
            ? "Aprueba la oferta económica en el Cotizador."
            : capReached
              ? capText(cap)
              : null;
  const checklist = fill?.result?.checklist?.length ? fill.result.checklist : read?.result?.checklist ?? [];
  const items = read?.result?.items ?? [];

  const createOffer = async () => {
    if (offerBusy || !read) return;
    const sig = JSON.stringify([tender.id, read.id]);
    if (offerKey.current?.sig !== sig) offerKey.current = { sig, key: newTenderCommandKey() };
    setOfferBusy(true);
    setOfferProblem(null);
    try {
      await createTenderOfferQuote(tender.id, read.id, offerKey.current.key);
      offerKey.current = null;
      toast("Oferta económica creada en el Cotizador");
      reload();
    } catch (err) {
      if (definitive(err)) offerKey.current = null;
      setOfferProblem(refusalText(err, cap));
    } finally {
      setOfferBusy(false);
    }
  };

  return (
    <>
      <header className="min-w-0 space-y-2">
        {tender.code ? <p className="text-[12px] tabular-nums text-ink-muted">{tender.code}</p> : null}
        <h1 className="break-words text-[19px] font-semibold tracking-tight text-ink">{tender.title}</h1>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-ink">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="break-words">{tender.institution.name ?? "Institución sin registrar"}</span>
            {tender.institution.confirmation === "machine_proposed" ? <Badge tone="warn" glyph={false}>por confirmar</Badge> : null}
          </span>
          <span>
            <span>Cierre {closingWhen(tender.closes_at)}</span>
            <span className="ml-1.5 text-[12px] text-ink-muted">({closingLabel(tender.closes_at, now)})</span>
          </span>
          {tender.portal_url ? <ExternalLink href={tender.portal_url}>Abrir en Mercado Público</ExternalLink> : null}
        </div>
      </header>

      <TenderStatusControl tenderId={tender.id} status={tender.status} version={tender.version} onChanged={reload} />

      {capReached ? (
        <p className="rounded-lg border border-warn/30 bg-warn-bg px-3 py-2 text-[13px] text-warn">{capText(cap)}</p>
      ) : null}

      <div role="status" aria-live="polite" className="empty:hidden">
        {readRunning ? <WorkingLine text={READING} /> : null}
        {fillRunning ? <WorkingLine text={FILLING} /> : null}
        {completion ? <p className="px-1 py-1 text-[13px] font-medium text-ink">{completion}</p> : null}
        {trouble ? <p className="px-1 py-1 text-[13px] font-medium text-warn">{trouble === "stopped" ? STOPPED : RETRYING}</p> : null}
      </div>

      <Section title="Anexos" named>
        <Card>
          {annexes.length === 0 ? (
            <Muted>Aún no hay anexos. Sube las bases, los formularios y las especificaciones técnicas.</Muted>
          ) : (
            <ul className="divide-y divide-line rounded-lg border border-line bg-canvas-raised">
              {annexes.map((a) => (
                <li key={a.document_id} className="flex min-w-0 flex-wrap justify-between gap-x-3 px-3 py-2 text-[13px]">
                  <span className="min-w-0 break-all text-ink">{a.filename}</span>
                  <span className="text-[12px] text-ink-muted">{fmtDateTime(a.attached_at)}</span>
                </li>
              ))}
            </ul>
          )}
          <AnnexUpload tenderId={tender.id} onUploaded={reload} />
          {!read ? (
            <StepButton
              label="Preparar oferta"
              reason={readReason}
              busy={prepare.busy === "read"}
              busyLabel="Pidiendo…"
              problem={prepare.problems.read}
              onClick={() => void prepare.run("read", null)}
            />
          ) : null}
        </Card>
      </Section>

      <Section
        title="Resumen"
        named
        aside={
          read && !readRunning ? (
            <StepButton
              label="Regenerar"
              ariaLabel="Regenerar la lectura de los anexos"
              variant="secondary"
              reason={readReason}
              busy={prepare.busy === "read"}
              busyLabel="Pidiendo…"
              problem={prepare.problems.read}
              onClick={() => void prepare.run("read", read.id)}
            />
          ) : undefined
        }
      >
        {!read ? (
          <Muted>Aún no se leen los anexos. Súbelos y usa «Preparar oferta».</Muted>
        ) : readRunning ? (
          <Muted>El resumen aparecerá aquí cuando termine la lectura.</Muted>
        ) : read.status !== "ready" ? (
          <Failure prep={read} cap={cap} />
        ) : (
          <ResumenList resumen={read.result?.resumen ?? {}} names={names} />
        )}
      </Section>

      <Section title="Checklist" named>
        {checklist.length > 0 || readReady ? (
          <ChecklistList lines={checklist} names={names} />
        ) : (
          <Muted>La lista de documentos sale de la lectura de los anexos.</Muted>
        )}
      </Section>

      <Section title="Oferta económica" named>
        <Card>
          {offer ? (
            <div className="space-y-2 rounded-lg border border-line bg-canvas-raised px-3 py-2">
              <p className="flex flex-wrap items-center gap-2 text-[13.5px] text-ink">
                <span className="font-medium">Cotización {offer.number}</span>
                <Badge tone={offerApproved ? "good" : "neutral"}>{labelOf(QUOTE_STATUS_ES, offer.status, "Sin estado")}</Badge>
              </p>
              <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-muted">
                <Money label="Neto" value={offer.neto} />
                <Money label="IVA" value={offer.iva} />
                <Money label="Total" value={offer.total} />
              </dl>
              {!offerApproved ? (
                <p className="text-[12.5px] text-ink-muted">Pon los precios y aprueba la cotización en el Cotizador para poder llenar los formularios.</p>
              ) : null}
              <a
                href={crmHash("cotizador", offer.quote_id)}
                onClick={(e) => {
                  e.preventDefault();
                  toCotizador(offer.quote_id);
                }}
                className={`${buttonClass("primary")} min-h-[44px] px-4 text-[13px]`}
              >
                Abrir en Cotizador
              </a>
            </div>
          ) : (
            <StepButton
              label="Crear oferta económica"
              reason={readReady ? null : NEEDS_READ}
              busy={offerBusy}
              busyLabel="Creando…"
              problem={offerProblem ?? undefined}
              onClick={() => void createOffer()}
            />
          )}
          {readReady && items.length > 0 ? <ItemsList items={items} productNames={productNames} match={read?.result?.match?.estado} /> : null}
        </Card>
      </Section>

      <Section
        title="Formularios llenados"
        named
        aside={
          fill && !fillRunning ? (
            <StepButton
              label="Regenerar"
              ariaLabel="Regenerar los formularios"
              variant="secondary"
              reason={fillReason}
              busy={prepare.busy === "fill"}
              busyLabel="Pidiendo…"
              problem={prepare.problems.fill}
              onClick={() => void prepare.run("fill", fill.id)}
            />
          ) : undefined
        }
      >
        <Card>
          {!fill ? (
            <StepButton
              label="Llenar formularios"
              reason={fillReason}
              busy={prepare.busy === "fill"}
              busyLabel="Pidiendo…"
              problem={prepare.problems.fill}
              onClick={() => void prepare.run("fill", null)}
            />
          ) : fillRunning ? (
            <Muted>Los formularios aparecerán aquí cuando termine el llenado.</Muted>
          ) : (
            <>
              {fill.status !== "ready" ? <Failure prep={fill} cap={cap} /> : null}
              <FilledForms tenderId={tender.id} fill={fill} />
            </>
          )}
        </Card>
      </Section>

      <Section title="Fichas técnicas" named>
        {fill && !fillRunning ? (
          <SpecSheets tenderId={tender.id} fill={fill} productNames={productNames} />
        ) : (
          <Muted>Las fichas de los productos de la oferta se adjuntan al llenar los formularios.</Muted>
        )}
      </Section>

      <Section title="Paquete" named>
        {fill && !fillRunning ? (
          <PackageBlock tenderId={tender.id} fill={fill} />
        ) : (
          <Muted>El paquete (.zip) con todo lo que se entrega se arma al llenar los formularios.</Muted>
        )}
      </Section>
    </>
  );
}

function WorkingLine({ text }: { text: string }) {
  return <p className="rounded-lg border border-info/30 bg-info-bg px-3 py-2 text-[13px] font-medium text-info">{text}</p>;
}

function Money({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex gap-1">
      <dt>{label}</dt>
      <dd className="font-medium tabular-nums text-ink">{value == null ? "—" : formatMoney(String(value), "CLP")}</dd>
    </div>
  );
}

function ItemsList({
  items,
  productNames,
  match,
}: {
  items: NonNullable<NonNullable<Preparation["result"]>["items"]>;
  productNames: ReadonlyMap<string, string>;
  match?: string;
}) {
  return (
    <div className="space-y-1">
      <p className="text-[12.5px] text-ink-muted">
        {items.length === 1 ? "La lectura encontró 1 ítem." : `La lectura encontró ${items.length} ítems.`}
        {match ? ` ${labelOf(MATCH_ES, match, "")}`.trimEnd() : ""}
      </p>
      <ol className="divide-y divide-line rounded-lg border border-line bg-canvas-raised">
        {items.map((it, i) => (
          <li key={`${it.linea}-${i}`} className="flex min-w-0 flex-col gap-0.5 px-3 py-2 text-[13px]">
            <span className="break-words text-ink">
              {it.linea}. {it.descripcion}
            </span>
            <span className="text-[12px] text-ink-muted">
              {[it.cantidad, it.unidad].filter(Boolean).join(" ") || "Cantidad sin indicar"}
              {it.product_id && productNames.get(it.product_id) ? ` · Sugerido: ${productNames.get(it.product_id)}` : ""}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
