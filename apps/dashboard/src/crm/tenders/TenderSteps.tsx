import type { ReactNode } from "react";
import { COTIZADOR_PREFERENCES, crmHash } from "../crmRoute";
import type { Preparation, TenderPageData } from "./tenderApi";

/**
 * «Pasos»: the tender's work as six steps — each done, under way, failed or still to do — and the
 * one to do now («Siguiente paso») spelled out, with a way to get there. Derived only from the page's
 * data: nothing here is stored, and a step is done when what it produces exists.
 */
export type StepState = "hecho" | "en_curso" | "ahora" | "pendiente" | "error";

export interface TenderStep {
  key: "anexos" | "lectura" | "oferta" | "oferente" | "formularios" | "postular";
  title: string;
  state: StepState;
  /** One short line: what the step has, or what it is waiting for. */
  detail: string;
  /** The element id of the section the step is done in (none for «Datos del oferente»: Preferencias). */
  target: string;
  /** What to do, in one sentence, when it is the next step. */
  todo: string;
}

export const STEP_TARGET = {
  anexos: "paso-anexos",
  lectura: "paso-resumen",
  oferta: "paso-oferta",
  formularios: "paso-formularios",
  postular: "paso-paquete",
} as const;

const busy = (p: Preparation | null) => p?.status === "queued" || p?.status === "running";

/** A step before its state: done, the machine working on it, or failed (its code; the step's
 * section says why, in Spanish — the rail only points there, so no line is said twice). */
type Draft = Omit<TenderStep, "state"> & { done: boolean; running?: boolean; failed?: string | null };

const FAILED = "No se pudo; mira el paso";

export function tenderSteps(data: TenderPageData): TenderStep[] {
  const { annexes, offer_quote: offer, bidder_ready: bidderReady, tender } = data;
  const read = data.preparations.read;
  const fill = data.preparations.fill;
  const readReady = read?.status === "ready";
  const fillReady = fill?.status === "ready";
  const offerApproved = offer?.status === "approved" || offer?.status === "sent";
  const docs = read?.result?.checklist?.length ?? 0;
  const items = read?.result?.items?.length ?? 0;

  const steps: Draft[] = [
    {
      key: "anexos",
      title: "Subir anexos",
      done: annexes.length > 0,
      detail: annexes.length === 0 ? "Bases, formularios y especificaciones" : annexes.length === 1 ? "1 archivo subido" : `${annexes.length} archivos subidos`,
      target: STEP_TARGET.anexos,
      todo: "Sube las bases, los anexos y las especificaciones técnicas (o el .zip de Mercado Público).",
    },
    {
      key: "lectura",
      title: "Leer las bases",
      done: readReady,
      running: busy(read),
      failed: read && !busy(read) && !readReady ? read.failure_code ?? read.status : null,
      detail: readReady ? `${docs} documentos · ${items} ítems` : busy(read) ? "Leyendo…" : "Resumen, documentos e ítems",
      target: STEP_TARGET.lectura,
      todo: read ? "Vuelve a leer los anexos con «Regenerar»." : "Usa «Preparar oferta» para que se lean los anexos.",
    },
    {
      key: "oferta",
      title: "Oferta económica",
      done: offerApproved,
      detail: offerApproved ? `Cotización ${offer!.number} aprobada` : offer ? `Cotización ${offer.number}: falta aprobar` : "Precios por ítem en el Cotizador",
      target: STEP_TARGET.oferta,
      todo: offer ? "Pon los precios y aprueba la cotización en el Cotizador." : "Crea la oferta económica: una cotización con un ítem por línea.",
    },
    {
      key: "oferente",
      title: "Datos del oferente",
      done: bidderReady,
      detail: bidderReady ? "Completos" : "RUT, representante legal y documentos",
      target: "",
      todo: "Completa los Datos para licitaciones en Preferencias; se guardan una vez para todas.",
    },
    {
      key: "formularios",
      title: "Formularios y fichas",
      done: fillReady,
      running: busy(fill),
      failed: fill && !busy(fill) && !fillReady ? fill.failure_code ?? fill.status : null,
      detail: fillReady ? "Formularios, fichas y paquete listos" : busy(fill) ? "Llenando…" : "Se llenan con la oferta y tus datos",
      target: STEP_TARGET.formularios,
      todo: "Usa «Llenar formularios»: llena los anexos, adjunta las fichas y arma el paquete.",
    },
    {
      key: "postular",
      title: "Postular en Mercado Público",
      done: tender.status === "postulada" || tender.status === "adjudicada",
      detail: "Firmar, subir y marcar «Postulada»",
      target: STEP_TARGET.postular,
      todo: "Firma lo que pide firma, sube el paquete en Mercado Público y marca la licitación como «Postulada».",
    },
  ];

  // The first step not done is the one to act on — or to wait for, while the machine works on it;
  // the ones after it are pending.
  let reached = false;
  return steps.map(({ done, running, failed, ...step }) => {
    let state: StepState = "hecho";
    if (!done) {
      state = reached ? "pendiente" : failed ? "error" : running ? "en_curso" : "ahora";
      reached = true;
    }
    return { ...step, detail: state === "error" ? FAILED : step.detail, state };
  });
}

const PREFERENCES_HREF = crmHash("cotizador", COTIZADOR_PREFERENCES);

const MARK: Record<StepState, { ring: string; label: string }> = {
  hecho: { ring: "border-good bg-good text-on-solid", label: "hecho" },
  en_curso: { ring: "border-info bg-info-bg text-info", label: "en curso" },
  ahora: { ring: "border-accent bg-accent text-on-solid", label: "siguiente" },
  pendiente: { ring: "border-line-strong bg-canvas-raised text-ink-muted", label: "pendiente" },
  error: { ring: "border-bad bg-bad-bg text-bad", label: "con problema" },
};

const NEXT_BOX: Record<StepState, string> = {
  hecho: "border-good/40 bg-good-bg",
  en_curso: "border-info/40 bg-info-bg",
  ahora: "border-accent/40 bg-accent/5",
  pendiente: "border-line bg-canvas-raised",
  error: "border-bad/40 bg-bad-bg",
};

function Mark({ n, state }: { n: number; state: StepState }) {
  return (
    <span
      aria-hidden="true"
      className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold tabular-nums ${MARK[state].ring}`}
    >
      {state === "hecho" ? "✓" : state === "error" ? "!" : n}
    </span>
  );
}

/** A link to where the step is done: its section on this page, or Preferencias for the bidder. */
function StepLink({ step, className, children }: { step: TenderStep; className: string; children: ReactNode }) {
  if (step.key === "oferente") {
    return (
      <a href={PREFERENCES_HREF} className={className}>
        {children}
      </a>
    );
  }
  return (
    <a
      href={`#${step.target}`}
      className={className}
      onClick={(e) => {
        e.preventDefault();
        document.getElementById(step.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
      }}
    >
      {children}
    </a>
  );
}

export function TenderSteps({ steps }: { steps: TenderStep[] }) {
  const done = steps.filter((s) => s.state === "hecho").length;
  const next = steps.find((s) => s.state !== "hecho");
  return (
    <nav aria-label="Pasos de la licitación" className="min-w-0 space-y-3">
      <div className="space-y-1.5">
        <p className="flex items-baseline justify-between text-[12px] text-ink-muted">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Pasos</span>
          <span className="tabular-nums">
            {done} de {steps.length} listos
          </span>
        </p>
        <div
          role="progressbar"
          aria-label="Avance de la postulación"
          aria-valuemin={0}
          aria-valuemax={steps.length}
          aria-valuenow={done}
          className="h-1.5 overflow-hidden rounded-full bg-canvas-sunken"
        >
          <div
            className="h-full rounded-full bg-good transition-[width] duration-300 motion-reduce:transition-none"
            style={{ width: `${(done / steps.length) * 100}%` }}
          />
        </div>
      </div>

      {next ? (
        <div className={`rounded-lg border px-3 py-2.5 ${NEXT_BOX[next.state]}`}>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{next.state === "en_curso" ? "En curso" : "Siguiente paso"}</p>
          <p className="mt-0.5 text-[13.5px] font-semibold text-ink">{next.title}</p>
          <p className="mt-0.5 text-[12.5px] leading-snug text-ink">{next.state === "en_curso" ? next.detail : next.todo}</p>
          <StepLink
            step={next}
            className="mt-1 inline-flex min-h-[44px] items-center text-[12.5px] font-medium text-accent hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            {next.key === "oferente" ? "Ir a Preferencias →" : "Ir al paso →"}
          </StepLink>
        </div>
      ) : (
        <p className="rounded-lg border border-good/40 bg-good-bg px-3 py-2.5 text-[13px] font-medium text-good">Todos los pasos están listos.</p>
      )}

      <ol className="space-y-0.5">
        {steps.map((s, i) => (
          <li key={s.key}>
            <StepLink
              step={s}
              className={`flex min-h-[44px] items-start gap-2.5 rounded-lg px-2 py-1.5 hover:bg-canvas-sunken/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${s.state === "ahora" || s.state === "error" ? "bg-canvas-sunken/50" : ""}`}
            >
              <Mark n={i + 1} state={s.state} />
              <span className="min-w-0">
                <span className={`block text-[13px] leading-5 ${s.state === "pendiente" ? "text-ink-muted" : "font-medium text-ink"}`}>
                  {s.title}
                  <span className="sr-only"> ({MARK[s.state].label})</span>
                </span>
                <span className={`block break-words text-[12px] leading-snug ${s.state === "error" ? "text-bad" : "text-ink-muted"}`}>{s.detail}</span>
              </span>
            </StepLink>
          </li>
        ))}
      </ol>
    </nav>
  );
}
