import { useCallback, useEffect, useRef, useState } from "react";
import { useMayRunCaseCommands } from "../caseCommands";
import type { CrmSection } from "../crmRoute";
import { Button, EmptyState, PageHeader, ResourceGate, Skeleton, toast } from "../ui";
import { useResource } from "../useResource";
import { ContactarDialog } from "./ContactarDialog";
import { DescartarDialog, MasTardeDialog } from "./DecisionDialogs";
import { EditProspectDrawer } from "./EditProspectDrawer";
import { EmailDraftDrawer } from "./EmailDraftDrawer";
import { ProspectCard, displayName } from "./ProspectCard";
import { ProspectDetail } from "./ProspectDetail";
import { SystemSearch } from "./SystemSearch";
import {
  ambiguousCandidates,
  contactProspect,
  fetchProspects,
  fetchSearchStatus,
  prospectRefusal,
  requestProspectSearch,
  reviewProspect,
  type OrganizationCandidate,
  type ProspectCounts,
  type ProspectItem,
  type ProspectList,
  type ProspectSearchStatus,
  type ProspectView,
} from "./prospectApi";
import { santiagoDay } from "./santiagoDate";

/**
 * «Prospectos» (spec 2026-10-23 §9): people who could buy from us, found by the machine and shown
 * best first, 25 at a time — by default only genuinely new ones (not in the CRM, not in our mail, never
 * anyone in Bajas). Each card says why now, what fits and how to reach them; «Contactar» puts them in
 * the CRM as a prospect, «Más tarde» brings them back on a day, «Descartar» (with a reason) never
 * shows them again — «Deshacer» undoes it. A card opens the full profile (`#/crm/prospectos/<id>`).
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

const VIEWS: { view: ProspectView; label: string }[] = [
  { view: "nuevos", label: "Nuevos" },
  { view: "mas_tarde", label: "Más tarde" },
  { view: "conocidos", label: "Ya conocidos con motivo nuevo" },
  { view: "contactados", label: "Contactados" },
  { view: "descartados", label: "Descartados" },
];
type Origin = "todos" | "fondos" | "laboratorios";
type Reach = "todos" | "con_correo" | "sin_correo";

const EMPTY: Record<ProspectView, string> = {
  nuevos: "No hay nuevos. Los próximos llegan cuando se cargue una nueva búsqueda; mientras tanto revisa Más tarde.",
  mas_tarde: "Nada guardado para más tarde.",
  conocidos: "Ningún contacto conocido con una novedad.",
  contactados: "Todavía no has contactado a nadie desde aquí.",
  descartados: "Nada descartado.",
};

function Tile({ label, value, title }: { label: string; value: string | number; title?: string }) {
  return (
    <div className="rounded-xl border border-line bg-canvas-raised px-4 py-3" title={title}>
      <p className="text-xs text-ink-muted">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-ink">{value}</p>
    </div>
  );
}

function Pills<T extends string>({ label, options, value, onChange, showLabel = false }: {
  label: string; options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; showLabel?: boolean;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      {showLabel ? <span className="mr-0.5 text-xs font-medium text-ink-muted">{label}</span> : null}
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={`min-h-8 rounded-full border px-3 py-1 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${
            value === o.value ? "border-brand-700 bg-brand-700 text-white" : "border-line-strong bg-canvas-raised text-ink hover:border-brand-600 hover:text-accent"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function matches(item: ProspectItem, origin: Origin, reach: Reach): boolean {
  const lab = item.source.startsWith("industria_");
  if (origin === "fondos" && lab) return false;
  if (origin === "laboratorios" && !lab) return false;
  if (reach === "con_correo" && !item.email) return false;
  if (reach === "sin_correo" && item.email) return false;
  return true;
}

/** «Búsqueda diaria» header bar: status, «Buscar ahora» button, poll while running. */
function DailySearchHeader({ canDecide }: { canDecide: boolean }) {
  const [status, setStatus] = useState<ProspectSearchStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchSearchStatus();
      setStatus(s);
      return s;
    } catch {
      return null;
    }
  }, []);

  // Initial load + poll every 8 s while running
  useEffect(() => {
    void refresh();
    const id = setInterval(async () => {
      const s = await refresh();
      if (s && !s.running) {
        clearInterval(id);
      }
    }, 8_000);
    pollRef.current = id;
    return () => clearInterval(id);
  }, [refresh]);

  // When a manual search is requested we start polling immediately
  const startSearch = async () => {
    setBusy(true);
    try {
      const r = await requestProspectSearch();
      if (r.queued) {
        toast("Búsqueda iniciada");
        void refresh();
      } else {
        toast("Ya hay una búsqueda en curso", "warn");
      }
    } catch {
      toast("No se pudo iniciar la búsqueda", "bad");
    } finally {
      setBusy(false);
    }
  };

  const run = status?.last_run;
  const running = status?.running ?? false;
  const finishedAt = run?.finished_at ? run.finished_at.slice(0, 10) : null;
  const added = run?.counts?.added ?? null;
  const poolLeft = run?.counts?.pool_left ?? null;

  let subtitle = "Búsqueda diaria 06:00 (Santiago)";
  if (running) subtitle += " · En ejecución…";
  else if (finishedAt) subtitle += ` · última: ${finishedAt}${added != null ? `, ${added} nuevos` : ""}`;
  if (status != null) subtitle += ` · ${status.unreviewed} por revisar`;
  if (poolLeft != null) subtitle += ` · ~${poolLeft} candidatos más en ANID`;

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-xl border border-line bg-canvas-raised px-4 py-3">
      <p className="text-sm text-ink-muted">{subtitle}</p>
      {canDecide ? (
        <Button onClick={() => void startSearch()} busy={busy || running} busyLabel="Buscando…">
          Buscar ahora
        </Button>
      ) : null}
    </div>
  );
}

export function ProspectosPage({ id = null, navigate, now }: { id?: string | null; navigate: Navigate; now?: Date }) {
  const clock = now ?? new Date();
  const today = santiagoDay(clock);
  const canDecide = useMayRunCaseCommands();
  const [view, setView] = useState<ProspectView>("nuevos");
  const [origin, setOrigin] = useState<Origin>("todos");
  const [reach, setReach] = useState<Reach>("todos");
  const [state, reload] = useResource(() => fetchProspects(view), [view]);
  const [more, setMore] = useState<{ view: ProspectView; items: ProspectItem[]; has_more: boolean } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busyId, setBusyId] = useState<string | null>(null);
  const [discarding, setDiscarding] = useState<ProspectItem[] | null>(null);
  const [editing, setEditing] = useState<ProspectItem | null>(null);
  const [preparingEmail, setPreparingEmail] = useState<ProspectItem | null>(null);
  const [postponing, setPostponing] = useState<ProspectItem[] | null>(null);
  const [choosing, setChoosing] = useState<{ item: ProspectItem; candidates: OrganizationCandidate[] } | null>(null);
  const [openId, setOpenId] = useState<string | null>(id);

  const changeView = (v: ProspectView) => {
    setView(v);
    setMore(null);
    setSelected(new Set());
  };
  const hide = (ids: string[]) => {
    setHidden((h) => new Set([...h, ...ids]));
    setSelected((s) => new Set([...s].filter((x) => !ids.includes(x))));
  };
  const open = (pid: string | null) => {
    setOpenId(pid);
    navigate("prospectos", pid);
  };

  const loadMore = async (list: ProspectList) => {
    setLoadingMore(true);
    try {
      // The server's next page starts after what it already sent, less what was acted on here
      // (those left this view upstream, so counting them would skip as many prospects).
      const sent = [...list.items, ...(more?.view === view ? more.items : [])];
      const shown = sent.filter((i) => !hidden.has(i.id)).length;
      const next = await fetchProspects(view, shown);
      setMore((m) => ({ view, items: [...(m?.view === view ? m.items : []), ...next.items], has_more: next.has_more }));
    } catch (err) {
      toast(prospectRefusal(err)?.message ?? "No se pudieron cargar más prospectos.", "bad");
    } finally {
      setLoadingMore(false);
    }
  };

  const contact = async (item: ProspectItem, choice: Record<string, unknown> = {}) => {
    setBusyId(item.id);
    try {
      await contactProspect({ finding_id: item.id, ...choice });
      setChoosing(null);
      hide([item.id]);
      toast("Agregado al CRM");
    } catch (err) {
      const candidates = ambiguousCandidates(err);
      if (candidates) setChoosing({ item, candidates });
      else toast(prospectRefusal(err)?.message ?? "No se pudo agregar al CRM.", "bad");
    } finally {
      setBusyId(null);
    }
  };

  const discard = async (items: ProspectItem[], reason: string) => {
    setBusyId(items[0]?.id ?? null);
    const done: ProspectItem[] = [];
    try {
      for (const item of items) {
        await reviewProspect({ finding_id: item.id, verdict: "descartar", reason });
        done.push(item);
      }
    } catch (err) {
      toast(prospectRefusal(err)?.message ?? "No se pudo descartar.", "bad");
    } finally {
      setBusyId(null);
      setDiscarding(null);
    }
    if (done.length === 0) return;
    hide(done.map((i) => i.id));
    toast(done.length === 1 ? "Descartado" : `${done.length} descartados`, "good", {
      action: {
        label: "Deshacer",
        onClick: () => {
          void Promise.all(done.map((i) => reviewProspect({ finding_id: i.id, verdict: "reabrir" })))
            .then(() => {
              setHidden((h) => new Set([...h].filter((x) => !done.some((i) => i.id === x))));
              reload();
              toast("Vuelve a la lista");
            })
            .catch((err: unknown) => toast(prospectRefusal(err)?.message ?? "No se pudo deshacer.", "bad"));
        },
      },
    });
  };

  const postpone = async (items: ProspectItem[], remindOn: string) => {
    setBusyId(items[0]?.id ?? null);
    const done: string[] = [];
    try {
      for (const item of items) {
        await reviewProspect({ finding_id: item.id, verdict: "mas_tarde", remind_on: remindOn });
        done.push(item.id);
      }
      toast(items.length === 1 ? "Guardado para más tarde" : `${items.length} guardados para más tarde`);
    } catch (err) {
      toast(prospectRefusal(err)?.message ?? "No se pudo guardar.", "bad");
    } finally {
      setBusyId(null);
      setPostponing(null);
      hide(done);
    }
  };

  const one = (items: ProspectItem[] | null) => (items && items.length === 1 ? displayName(items[0]) : `${items?.length ?? 0} prospectos`);

  return (
    <div className="mx-auto max-w-5xl space-y-5 px-4 py-5 sm:px-6">
      <PageHeader title="Prospectos" subtitle="Gente nueva que podría comprarnos. Revisa, contacta o descarta." />
      <DailySearchHeader canDecide={canDecide} />
      <SystemSearch onOpenProspect={open} />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={5} cards />}>
        {(list) => {
          const counts: ProspectCounts = list.counts;
          const all = [...list.items, ...(more?.view === view ? more.items : [])].filter((i) => !hidden.has(i.id));
          const items = all.filter((i) => matches(i, origin, reach));
          const hasMore = more?.view === view ? more.has_more : list.has_more;
          const picked = items.filter((i) => selected.has(i.id));
          return (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Tile label="Nuevos hoy" value={counts.nuevos} />
                <Tile label="Por revisar" value={counts.por_revisar} />
                <Tile label="Contactados este mes" value={counts.contactados_mes} />
                <Tile label="Respondieron" value="—" title="Aún no se mide: las respuestas no se cuentan aquí" />
              </div>
              {counts.omitidos_bajas > 0 ? (
                <p className="text-xs text-ink-muted">{counts.omitidos_bajas} omitidos por bajas</p>
              ) : null}
              <div className="space-y-2">
                <Pills label="Vista" options={VIEWS.map((v) => ({ value: v.view, label: v.label }))} value={view} onChange={changeView} />
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  <Pills<Origin> label="Origen" showLabel value={origin} onChange={setOrigin}
                    options={[{ value: "todos", label: "Todos" }, { value: "fondos", label: "Fondos" }, { value: "laboratorios", label: "Laboratorios" }]} />
                  <Pills<Reach> label="Contacto" showLabel value={reach} onChange={setReach}
                    options={[{ value: "todos", label: "Todos" }, { value: "con_correo", label: "Con correo" }, { value: "sin_correo", label: "Sin correo — llamar" }]} />
                </div>
              </div>
              {canDecide && picked.length > 0 ? (
                <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-brand-600/30 bg-brand-50 px-4 py-2" role="region" aria-label="Selección">
                  <span className="text-[13px] font-medium text-accent">{picked.length} seleccionados</span>
                  <Button onClick={() => setPostponing(picked)}>Más tarde</Button>
                  <Button variant="quiet" onClick={() => setDiscarding(picked)}>Descartar</Button>
                  <button type="button" onClick={() => setSelected(new Set())} className="ml-auto text-xs text-ink-muted hover:text-ink">Quitar selección</button>
                </div>
              ) : null}
              {items.length === 0 ? (
                <EmptyState title={EMPTY[view]} />
              ) : (
                <div className="space-y-3">
                  {items.map((item) => (
                    <ProspectCard
                      key={item.id}
                      item={item}
                      now={clock}
                      canDecide={canDecide && view !== "contactados"}
                      selected={selected.has(item.id)}
                      busy={busyId === item.id}
                      onToggle={() => setSelected((s) => {
                        const n = new Set(s);
                        if (n.has(item.id)) n.delete(item.id);
                        else n.add(item.id);
                        return n;
                      })}
                      onOpen={() => open(item.id)}
                      onContact={() => void contact(item)}
                      onLater={() => setPostponing([item])}
                      onDiscard={() => setDiscarding([item])}
                      onEdit={() => setEditing(item)}
                      onPrepareEmail={item.email ? () => setPreparingEmail(item) : undefined}
                    />
                  ))}
                </div>
              )}
              {hasMore ? (
                <div className="flex justify-center">
                  <Button onClick={() => void loadMore(list)} busy={loadingMore} busyLabel="Cargando…">
                    Ver más
                  </Button>
                </div>
              ) : null}
            </>
          );
        }}
      </ResourceGate>
      {discarding ? (
        <DescartarDialog title={`Descartar a ${one(discarding)}`} busy={busyId !== null}
          onClose={() => setDiscarding(null)} onConfirm={(reason) => void discard(discarding, reason)} />
      ) : null}
      {postponing ? (
        <MasTardeDialog title={`Volver a mostrar a ${one(postponing)}`} today={today} busy={busyId !== null}
          onClose={() => setPostponing(null)} onConfirm={(day) => void postpone(postponing, day)} />
      ) : null}
      {choosing ? (
        <ContactarDialog candidates={choosing.candidates} busy={busyId !== null}
          onClose={() => setChoosing(null)} onChoose={(choice) => void contact(choosing.item, choice)} />
      ) : null}
      {openId ? <ProspectDetail id={openId} onClose={() => open(null)} /> : null}
      {editing ? (
        <EditProspectDrawer item={editing} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }} />
      ) : null}
      {preparingEmail ? (
        <EmailDraftDrawer
          findingId={preparingEmail.id}
          existingDraft={preparingEmail.email_draft}
          onClose={() => setPreparingEmail(null)}
          onDrafted={reload}
        />
      ) : null}
    </div>
  );
}
