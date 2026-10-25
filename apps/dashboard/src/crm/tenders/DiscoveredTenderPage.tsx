import { useCallback, useState, type CSSProperties } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import { crmHash, type CrmSection } from "../crmRoute";
import { formatMoney } from "../money";
import { Badge, Button, Panel, ResourceGate, Skeleton, buttonClass, isSafeExternalHref, toast } from "../ui";
import { useResource } from "../useResource";
import { NewTenderDialog } from "./NewTenderDialog";
import {
  fetchDiscoveredTender,
  newTenderCommandKey,
  reviewNotice,
  tenderRefusal,
  type DiscoveredNoticePage,
  type NoticeVerdict,
} from "./tenderApi";
import { closingLabel, closingWhen } from "./tenderTime";

/**
 * One Mercado Público notice of «En Mercado Público», before any case exists: everything the API
 * says about it, in the dashboard's panels.
 *
 * - **The header**: code, families, estado, title, institution; «Abrir en Mercado Público ↗» (the
 *   notice's own page, where its bases and anexos are) and «Preparar oferta» (sales and admin: opens
 *   it through «Nueva licitación», code filled in) — or «Abrir» on the case already made from it;
 *   «Nos interesa» / «No es nuestro» (sales and admin, `review-notice`) and the verdict once given.
 * - **A stat line**: when it closes, the estimate, the items, what of the catalog it names.
 * - **Fechas**: every date of the head on one track (publication … award), the passed ones filled,
 *   the next one marked; Santiago time.
 * - **Lo que piden**: every item with quantity, its category and what it matched.
 * - **Documentos**: the bases and anexos live on Mercado Público's page; the link is here.
 * - **Datos**: the head's facts (type, buying unit, contract length, delivery address, complaints).
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

export function DiscoveredTenderPage({ noticeId, navigate, now }: { noticeId: string; navigate: Navigate; now?: Date }) {
  const load = useCallback(() => fetchDiscoveredTender(noticeId), [noticeId]);
  const [state, reload] = useResource(load, [noticeId]);
  const { session } = useAuthSession();
  const mayOpen = session.kind === "signed_in" && ROLES_THAT_DECIDE_CASES.has(session.operator.role);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const at = now ?? new Date();

  async function review(verdict: NoticeVerdict) {
    if (busy) return;
    setBusy(true);
    try {
      await reviewNotice({ notice_id: noticeId, verdict }, newTenderCommandKey());
      toast(verdict === "interesa" ? "Marcada: te interesa." : "Descartada: ya no aparece en la lista.");
      reload();
    } catch (err) {
      toast(tenderRefusal(err)?.message ?? "No se pudo guardar. Intenta de nuevo.", "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <a
        href={crmHash("licitaciones")}
        onClick={(e) => {
          e.preventDefault();
          navigate("licitaciones");
        }}
        className="text-xs font-medium text-accent hover:underline"
      >
        ← En Mercado Público
      </a>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
        {(n) => (
          <>
            <Header notice={n} navigate={navigate} mayOpen={mayOpen} onPrepare={() => setAsking(true)} busy={busy} onReview={(v) => void review(v)} />
            <Stats notice={n} now={at} />
            <Dates notice={n} now={at} />
            <div className="flex flex-wrap items-start gap-4">
              <div className="min-w-0 flex-[999_1_600px] space-y-4">
                <Items notice={n} />
                <Documents notice={n} />
              </div>
              <div className="min-w-0 flex-[1_1_320px] space-y-4">
                <Facts notice={n} />
              </div>
            </div>
            {asking ? <NewTenderDialog navigate={navigate} initialReference={n.code} onClose={() => setAsking(false)} /> : null}
          </>
        )}
      </ResourceGate>
    </div>
  );
}

function PortalButton({ href, code, primary }: { href: string; code: string; primary?: boolean }) {
  if (!isSafeExternalHref(href)) return null;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" aria-label={`Abrir ${code} en Mercado Público`} className={buttonClass(primary ? "primary" : "secondary")}>
      Abrir en Mercado Público <span aria-hidden="true">↗</span>
    </a>
  );
}

function Header({
  notice: n,
  navigate,
  mayOpen,
  onPrepare,
  busy,
  onReview,
}: {
  notice: DiscoveredNoticePage;
  navigate: Navigate;
  mayOpen: boolean;
  onPrepare: () => void;
  busy: boolean;
  onReview: (verdict: NoticeVerdict) => void;
}) {
  const verdict = n.review?.verdict ?? null;
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
      <div className="min-w-0 flex-[1_1_520px]">
        <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted">
          <span className="tabular-nums">{n.code}</span>
          {verdict ? (
            <Badge tone={verdict === "interesa" ? "good" : "neutral"} title={n.review?.by ? `Marcada por ${n.review.by}` : undefined}>
              {verdict === "interesa" ? "te interesa" : "descartada"}
            </Badge>
          ) : null}
          {n.families.map((f) => (
            <Badge key={f.key} tone="brand" glyph={false}>
              {f.label}
            </Badge>
          ))}
          {n.source_status ? (
            <Badge tone={n.open ? "good" : "neutral"} glyph={false}>
              {n.source_status}
            </Badge>
          ) : null}
        </div>
        <h1 className="break-words text-[17px] font-semibold leading-snug tracking-tight text-ink">{n.title}</h1>
        <p className="mt-0.5 text-xs text-ink-muted">
          {n.buyer ?? "Institución por leer"}
          {n.region ? ` · ${n.region.trim()}` : ""}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <PortalButton href={n.portal_url} code={n.code} primary={!mayOpen || n.opportunity_id !== null} />
        {n.opportunity_id ? (
          <a
            href={crmHash("licitaciones", n.opportunity_id)}
            onClick={(e) => {
              e.preventDefault();
              navigate("licitaciones", n.opportunity_id);
            }}
            className={buttonClass("secondary")}
          >
            Abrir la licitación
          </a>
        ) : mayOpen && n.open ? (
          <Button variant="primary" onClick={onPrepare}>
            Preparar oferta
          </Button>
        ) : null}
        {mayOpen && verdict !== "interesa" ? (
          <Button disabled={busy} onClick={() => onReview("interesa")}>
            Nos interesa
          </Button>
        ) : null}
        {mayOpen && verdict !== "no_es_nuestro" ? (
          <Button variant="quiet" disabled={busy} onClick={() => onReview("no_es_nuestro")}>
            No es nuestro
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function Stats({ notice: n, now }: { notice: DiscoveredNoticePage; now: Date }) {
  const amount = n.estimated_amount ? formatMoney(String(n.estimated_amount.amount), n.estimated_amount.currency) : null;
  const leftMs = n.closes_at ? new Date(n.closes_at).getTime() - now.getTime() : Number.NaN;
  const catalogItems = n.items.filter((i) => (i.families?.length ?? 0) > 0 || (i.terms?.length ?? 0) > 0).length;
  const items: { label: string; value: string; tone?: string }[] = [
    { label: "Cierra", value: `${closingLabel(n.closes_at, now)} · ${closingWhen(n.closes_at)}`, tone: leftMs > 0 && leftMs <= 2 * 86_400_000 ? "text-bad" : "text-ink" },
    { label: "Estimado", value: amount ?? "sin monto publicado" },
    { label: "Ítems", value: String(n.item_count) },
    { label: "Calzan con tu catálogo", value: `${catalogItems} de ${n.item_count}`, tone: catalogItems > 0 ? "text-accent" : "text-ink" },
  ];
  return (
    <dl className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
      {items.map((s, i) => (
        <div key={s.label} className="flex items-center gap-3">
          {i > 0 ? <span aria-hidden="true" className="h-3 w-px bg-line-strong" /> : null}
          <div className="flex items-baseline gap-1.5">
            <dt>{s.label}</dt>
            <dd className={`font-semibold tabular-nums ${s.tone ?? "text-ink"}`}>{s.value}</dd>
          </div>
        </div>
      ))}
      {(n.terms ?? []).length > 0 ? (
        <div className="flex items-center gap-3">
          <span aria-hidden="true" className="h-3 w-px bg-line-strong" />
          <span>
            Calza con: <span className="font-medium text-accent">{(n.terms ?? []).join(" · ")}</span>
          </span>
        </div>
      ) : null}
    </dl>
  );
}

const DAY = new Intl.DateTimeFormat("es-CL", { timeZone: "America/Santiago", day: "2-digit", month: "short" });
const HOUR = new Intl.DateTimeFormat("es-CL", { timeZone: "America/Santiago", hour: "2-digit", minute: "2-digit", hour12: false });

function Dates({ notice: n, now }: { notice: DiscoveredNoticePage; now: Date }) {
  if (n.dates.length === 0) {
    return (
      <Panel title="Fechas">
        <p className="px-3 py-3 text-xs text-ink-muted">{n.detail ? "Mercado Público no publica fechas para esta licitación." : "Las fechas llegan con el detalle, en la próxima revisión."}</p>
      </Panel>
    );
  }
  const passed = n.dates.filter((d) => new Date(d.at).getTime() <= now.getTime()).length;
  const nextIndex = passed < n.dates.length ? passed : -1;
  // Each dot sits at its column's start: the track reaches halfway to the next date, or the end.
  const fill = passed === 0 ? 0 : passed >= n.dates.length ? 100 : ((passed - 0.5) / n.dates.length) * 100;
  return (
    <Panel title="Fechas" note="hora de Santiago">
      <div className="overflow-x-auto">
        <ol className="relative grid min-w-[640px] gap-2 px-4 pb-3 pt-4" style={{ gridTemplateColumns: `repeat(${n.dates.length}, minmax(0, 1fr))` }}>
          <span aria-hidden="true" className="absolute left-4 right-4 top-[21px] h-0.5 bg-line" />
          <span aria-hidden="true" className="crm-grow-x absolute left-4 top-[21px] h-0.5 bg-brand-600" style={{ width: `calc((100% - 2rem) * ${fill / 100})` } as CSSProperties} />
          {n.dates.map((d, i) => {
            const done = i < passed;
            const next = i === nextIndex;
            const at = new Date(d.at);
            return (
              <li key={d.key} className="relative flex flex-col gap-1" aria-current={next ? "step" : undefined}>
                <span className="relative flex h-3 w-3">
                  {next ? <span aria-hidden="true" className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-600 opacity-50 motion-reduce:hidden" /> : null}
                  <span
                    aria-hidden="true"
                    className={`relative inline-flex h-3 w-3 rounded-full border-2 ${
                      done ? "border-brand-100 bg-brand-600" : next ? "border-brand-600 bg-canvas-raised" : "border-line-strong bg-canvas-raised"
                    }`}
                  />
                </span>
                <span className={`text-xs font-semibold ${next ? "text-accent" : "text-ink"}`}>{d.label}</span>
                <span className="text-[11px] tabular-nums text-ink-muted">
                  {DAY.format(at)} · {HOUR.format(at)}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    </Panel>
  );
}

function Items({ notice: n }: { notice: DiscoveredNoticePage }) {
  return (
    <Panel title="Lo que piden" note={n.detail ? `${n.item_count} ${n.item_count === 1 ? "ítem" : "ítems"} de Mercado Público` : "el detalle llega en la próxima revisión"}>
      {n.items.length === 0 ? (
        <p className="px-3 py-3 text-xs text-ink-muted">{n.description ?? "Sin ítems publicados."}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr className="bg-canvas-sunken text-left text-[11px] uppercase tracking-wider text-ink-faint">
                <th scope="col" className="px-3 py-1.5 font-semibold">Cant.</th>
                <th scope="col" className="px-3 py-1.5 font-semibold">Ítem</th>
                <th scope="col" className="px-3 py-1.5 font-semibold">Categoría</th>
                <th scope="col" className="px-3 py-1.5 font-semibold">Calza con</th>
              </tr>
            </thead>
            <tbody>
              {n.items.map((item, i) => {
                const hit = (item.families ?? []).map((f) => f.label).concat(item.terms ?? []);
                return (
                  <tr key={i} className="crm-rise border-t border-line align-top transition-colors hover:bg-canvas-sunken/40" style={{ "--i": Math.min(i, 12) } as CSSProperties}>
                    <td className="whitespace-nowrap px-3 py-2 font-semibold tabular-nums">
                      {item.quantity ?? "—"}
                      {item.unit ? <span className="ml-1 text-[11px] font-normal text-ink-muted">{item.unit.toLowerCase()}</span> : null}
                    </td>
                    <td className="px-3 py-2">
                      <span className="block font-medium text-ink">{item.name ?? "Ítem"}</span>
                      {item.description && item.description !== item.name ? <span className="block text-xs text-ink-muted">{item.description}</span> : null}
                    </td>
                    <td className="px-3 py-2 text-xs text-ink-muted">{item.category ?? "—"}</td>
                    <td className="px-3 py-2">
                      {hit.length > 0 ? (
                        <span className="flex flex-wrap gap-1">
                          {hit.map((h) => (
                            <Badge key={h} tone="brand" glyph={false}>
                              {h}
                            </Badge>
                          ))}
                        </span>
                      ) : item.lab_equipment ? (
                        <Badge tone="neutral" glyph={false}>
                          equipo de laboratorio
                        </Badge>
                      ) : (
                        <span className="text-xs text-ink-faint">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function Documents({ notice: n }: { notice: DiscoveredNoticePage }) {
  return (
    <Panel title="Documentos" note="bases, anexos y formularios">
      <div className="flex flex-wrap items-center gap-3 px-3 py-3">
        <p className="min-w-[220px] flex-1 text-xs text-ink-muted">
          Las bases y los anexos están en la ficha de Mercado Público. Ábrela para leerlos y descargarlos; al preparar la oferta
          se suben aquí.
        </p>
        <PortalButton href={n.portal_url} code={n.code} />
      </div>
    </Panel>
  );
}

function Facts({ notice: n }: { notice: DiscoveredNoticePage }) {
  return (
    <>
      {n.description ? (
        <Panel title="Descripción">
          <p className="whitespace-pre-line px-3 py-3 text-[13px] leading-relaxed text-ink">{n.description}</p>
        </Panel>
      ) : null}
      <Panel title="Datos de la licitación">
        {n.facts.length === 0 ? (
          <p className="px-3 py-3 text-xs text-ink-muted">Los datos llegan con el detalle, en la próxima revisión.</p>
        ) : (
          <dl className="px-3 py-1">
            {n.facts.map((f) => (
              <div key={f.label} className="flex justify-between gap-3 border-b border-line py-1.5 text-xs last:border-b-0">
                <dt className="text-ink-muted">{f.label}</dt>
                <dd className="text-right font-medium text-ink">{f.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </Panel>
    </>
  );
}
