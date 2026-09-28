import { useEffect, useMemo, useRef, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import type { CampaignTotals, TotalKey } from "../crmTypes";
import { Badge, Drawer, ExternalLink, ResourceGate, SearchInput, Section, Skeleton, fmtInt, type Tone } from "../ui";
import { useResource } from "../useResource";
import { santiagoTime } from "./calendar";
import { FILTER_TOTALS, TOTAL_HINT, TOTAL_LABEL } from "./campaignTotals";
import { EvidenceList, useInterestLabel } from "./interestEvidence";
import { fetchCampaignRecipients } from "./marketingApi";
import type { EquipmentTaxonomy, HistoryRecipient, RecipientPage, RecipientQuery } from "./marketingTypes";

const OUTCOME: Record<HistoryRecipient["outcome"], { label: string; tone: Tone }> = {
  sent: { label: "Enviado", tone: "good" },
  bounced: { label: "Rebotado", tone: "bad" },
  excluded: { label: "Excluido", tone: "neutral" },
  rejected: { label: "Rechazado", tone: "warn" },
  unsent: { label: "No enviado", tone: "info" },
};

const REJECTION: Record<string, string> = {
  permanent: "Rechazo permanente",
  transient: "Rechazo transitorio",
  invalid_address: "Dirección no válida",
};

const PAGE_SIZE = 50;
const ADDRESS_ROLES = new Set(["sales", "admin"]);

function fmtSent(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("es-CL", { day: "2-digit", month: "short", year: "numeric", timeZone: "America/Santiago" })} · ${santiagoTime(value)}`;
}

/**
 * Destinatarios: the recorded recipient rows of one campaign, a server-side page at a time,
 * filtered by exactly the total the operator clicked. A viewer reads masked addresses and
 * searches names only — the list never answers "is this address in the audience?".
 */
export function CampaignRecipients({
  campaignId,
  totals,
  initialTotal = "audience",
  taxonomy,
  repliesNotSynced = false,
}: {
  campaignId: string;
  totals: CampaignTotals | undefined;
  initialTotal?: TotalKey;
  taxonomy: EquipmentTaxonomy | null;
  /** Nothing was ever stored: the responses count is unknown, never zero. */
  repliesNotSynced?: boolean;
}) {
  const { session } = useAuthSession();
  const role = session.kind === "signed_in" ? session.operator.role : null;
  const seesAddresses = role !== null && ADDRESS_ROLES.has(role);
  const [query, setQuery] = useState<RecipientQuery>({ total: initialTotal, page: 1, page_size: PAGE_SIZE });
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<HistoryRecipient | null>(null);

  // One read per settled search, not per keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => {
      setQuery((q) => ((q.q ?? "") === search.trim() ? q : { ...q, q: search.trim() || undefined, page: 1 }));
    }, 300);
    return () => window.clearTimeout(t);
  }, [search]);
  useEffect(() => setQuery((q) => ({ ...q, total: initialTotal, page: 1 })), [initialTotal]);

  const key = JSON.stringify(query);
  const [state, reload] = useResource(() => fetchCampaignRecipients(campaignId, query), [campaignId, key]);
  // Keep the last page on screen while the next one loads: paging should not flash a skeleton.
  const last = useRef<RecipientPage | null>(null);
  if (state.kind === "ready") last.current = state.data;
  const shown = state.kind === "ready" ? state.data : state.kind === "loading" ? last.current : null;
  const counts = shown?.totals ?? totals;

  const set = (patch: Partial<RecipientQuery>) => setQuery((q) => ({ ...q, ...patch, page: patch.page ?? 1 }));

  return (
    <div className="space-y-3" data-testid="campaign-recipients">
      <div role="group" aria-label="Filtrar destinatarios por total" className="flex flex-wrap gap-1.5">
        {FILTER_TOTALS.map((k) => {
          const active = query.total === k;
          return (
            <button
              key={k}
              type="button"
              aria-pressed={active}
              title={TOTAL_HINT[k]}
              onClick={() => set({ total: k, reason: k === "excluded" || k === "blocked" ? query.reason : undefined })}
              className={`inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium ${
                active ? "border-ink bg-ink text-white" : "border-line bg-canvas-raised text-ink-muted hover:border-line-strong hover:text-ink"
              }`}
              data-testid={`filter-${k}`}
            >
              {TOTAL_LABEL[k]}
              <span className={`tabular-nums ${active ? "text-white/80" : "text-ink-faint"}`}>
                {k === "responses" && repliesNotSynced ? "no sincronizadas" : counts ? fmtInt(counts[k]) : "…"}
              </span>
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={search}
          onChange={setSearch}
          label="Buscar destinatarios"
          placeholder={seesAddresses ? "Buscar por dirección, persona o institución" : "Buscar por persona o institución del CRM"}
        />
        <select
          aria-label="Identidad"
          value={query.identity ?? ""}
          onChange={(e) => set({ identity: (e.target.value || undefined) as RecipientQuery["identity"] })}
          className="h-7 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink"
        >
          <option value="">Toda identidad</option>
          <option value="crm_person">Persona del CRM</option>
          <option value="historical_address">Dirección histórica</option>
        </select>
        {query.total === "excluded" || query.total === "blocked" ? (
          <select
            aria-label="Motivo de exclusión"
            value={query.reason ?? ""}
            onChange={(e) => set({ reason: e.target.value || undefined })}
            className="h-7 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink"
          >
            <option value="">Todo motivo</option>
            <option value="block">Dirección bloqueada</option>
            <option value="block_domain">Dominio bloqueado</option>
            <option value="manual_inactive">Marcada inactiva (V1)</option>
            <option value="manual_hold">Retenida por un operador</option>
            <option value="prior_contact">Contacto previo</option>
            <option value="cooldown">En período de espera</option>
            <option value="policy_supplier">Proveedor</option>
          </select>
        ) : null}
        {!seesAddresses ? (
          <span className="text-[11px] text-ink-faint" data-testid="viewer-search-note">
            Tu rol ve direcciones enmascaradas; la búsqueda sólo cubre nombres registrados en el CRM.
          </span>
        ) : null}
      </div>
      {state.kind !== "loading" && state.kind !== "ready" ? (
        <ResourceGate state={state} reload={reload}>
          {() => null}
        </ResourceGate>
      ) : shown ? (
        <RecipientTable page={shown} loading={state.kind === "loading"} onOpen={setOpen} onPage={(page) => set({ page })} />
      ) : (
        <Skeleton rows={8} />
      )}
      <RecipientDrawer recipient={open} taxonomy={taxonomy} onClose={() => setOpen(null)} />
    </div>
  );
}

function Who({ r }: { r: HistoryRecipient }) {
  return (
    <div className="min-w-0">
      {r.identity === "crm_person" ? (
        <>
          <p className="truncate font-medium text-ink">{r.person_name}</p>
          <p className="truncate text-[11px] text-ink-muted">
            <span className="font-mono">{r.address}</span> · <span className="text-brand-700">Persona del CRM</span>
          </p>
        </>
      ) : (
        <>
          <p className="truncate font-mono text-[12px] text-ink" title={r.address}>
            {r.address}
          </p>
          <p className="text-[11px] text-ink-faint">Dirección histórica</p>
        </>
      )}
    </div>
  );
}

function Detail({ r }: { r: HistoryRecipient }) {
  const bits: { text: string; tone: Tone }[] = [];
  if (r.bounce) bits.push({ text: "Rebote registrado", tone: "bad" });
  if (r.rejection) bits.push({ text: REJECTION[r.rejection] ?? r.rejection, tone: "warn" });
  for (const x of r.exclusion_reasons) bits.push({ text: x.label, tone: "neutral" });
  if (r.baja === "registered") bits.push({ text: "BAJA registrada", tone: "bad" });
  if (r.baja === "pending_review") bits.push({ text: "BAJA en revisión", tone: "warn" });
  if (r.replies) bits.push({ text: r.replies === 1 ? "1 respuesta" : `${r.replies} respuestas`, tone: "info" });
  if (!bits.length) return <span className="text-ink-faint">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {bits.map((b) => (
        <Badge key={b.text} tone={b.tone} glyph={false}>
          {b.text}
        </Badge>
      ))}
    </div>
  );
}

function Interest({ r }: { r: HistoryRecipient }) {
  const n = r.interests?.length ?? 0;
  if (!n) return <span className="text-ink-faint">Sin interés registrado</span>;
  return <span className="text-ink">{n === 1 ? "1 evidencia" : `${n} evidencias`}</span>;
}

function RecipientTable({
  page,
  loading,
  onOpen,
  onPage,
}: {
  page: RecipientPage;
  loading: boolean;
  onOpen: (r: HistoryRecipient) => void;
  onPage: (page: number) => void;
}) {
  const from = page.total_rows === 0 ? 0 : (page.page - 1) * page.page_size + 1;
  const to = Math.min(page.page * page.page_size, page.total_rows);
  return (
    <section
      className={`min-w-0 overflow-hidden rounded-lg border border-line bg-canvas-raised transition-opacity ${loading ? "opacity-60" : ""}`}
      aria-busy={loading}
    >
      {page.rows.length === 0 ? (
        <p className="px-4 py-8 text-center text-xs text-ink-muted" data-testid="recipients-empty">
          Ningún destinatario registrado coincide con este filtro.
        </p>
      ) : (
        <>
          {/* Desktop: a compact table with a shaded header. */}
          <table className="hidden w-full table-fixed text-xs md:table" data-testid="recipients-table">
            <thead className="bg-canvas-sunken text-left text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
              <tr>
                <th className="w-[32%] px-3 py-2">Destinatario</th>
                <th className="w-[18%] px-3 py-2">Institución</th>
                <th className="w-[15%] px-3 py-2">Envío</th>
                <th className="w-[10%] px-3 py-2">Resultado</th>
                <th className="w-[15%] px-3 py-2">Detalle</th>
                <th className="w-[10%] px-3 py-2">Interés</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {page.rows.map((r) => (
                <tr
                  key={r.recipient_id}
                  tabIndex={0}
                  onClick={() => onOpen(r)}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen(r))}
                  className="cursor-pointer align-top hover:bg-canvas-sunken/60 focus:bg-canvas-sunken/60 focus:outline-none"
                  data-testid="recipient-row"
                >
                  <td className="px-3 py-2">
                    <Who r={r} />
                  </td>
                  <td className="truncate px-3 py-2" title={r.organization_name ?? undefined}>
                    {r.organization_name ? <span className="text-ink">{r.organization_name}</span> : <span className="text-ink-faint">Sin institución vinculada</span>}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-ink-muted">{fmtSent(r.sent_at)}</td>
                  <td className="px-3 py-2">
                    <Badge tone={OUTCOME[r.outcome].tone}>{OUTCOME[r.outcome].label}</Badge>
                  </td>
                  <td className="px-3 py-2">
                    <Detail r={r} />
                  </td>
                  <td className="px-3 py-2">
                    <Interest r={r} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {/* Mobile: one card per recipient; nothing scrolls sideways. */}
          <ul className="divide-y divide-line md:hidden" data-testid="recipients-cards">
            {page.rows.map((r) => (
              <li key={r.recipient_id}>
                <button type="button" onClick={() => onOpen(r)} className="block w-full space-y-1.5 px-3 py-2.5 text-left text-xs">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <Who r={r} />
                    </div>
                    <Badge tone={OUTCOME[r.outcome].tone}>{OUTCOME[r.outcome].label}</Badge>
                  </div>
                  <p className="truncate text-[11px] text-ink-muted">
                    {r.organization_name ?? "Sin institución vinculada"} · {fmtSent(r.sent_at)}
                  </p>
                  <Detail r={r} />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      <footer className="flex flex-wrap items-center gap-2 border-t border-line bg-canvas-sunken/50 px-3 py-2 text-[11px] text-ink-muted">
        <span data-testid="recipients-range">
          {fmtInt(from)}–{fmtInt(to)} de <b className="text-ink">{fmtInt(page.total_rows)}</b> destinatarios
        </span>
        <span className="text-ink-faint">· {page.storage.table} en {page.storage.database}</span>
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            disabled={page.page <= 1 || loading}
            onClick={() => onPage(page.page - 1)}
            className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 font-medium text-ink disabled:opacity-40"
          >
            ← Anterior
          </button>
          <span className="px-1 tabular-nums">
            {page.page} / {page.pages}
          </span>
          <button
            type="button"
            disabled={page.page >= page.pages || loading}
            onClick={() => onPage(page.page + 1)}
            className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 font-medium text-ink disabled:opacity-40"
            data-testid="recipients-next"
          >
            Siguiente →
          </button>
        </div>
      </footer>
    </section>
  );
}

function RecipientDrawer({
  recipient: r,
  taxonomy,
  onClose,
}: {
  recipient: HistoryRecipient | null;
  taxonomy: EquipmentTaxonomy | null;
  onClose: () => void;
}) {
  return (
    <Drawer
      open={r !== null}
      onClose={onClose}
      title={r ? (r.identity === "crm_person" ? r.person_name ?? r.address : r.address) : ""}
      subtitle={r ? (r.identity === "crm_person" ? "Persona del CRM" : "Dirección histórica · sin persona en el CRM") : null}
    >
      {r ? <DrawerBody r={r} taxonomy={taxonomy} /> : null}
    </Drawer>
  );
}

function DrawerBody({ r, taxonomy }: { r: HistoryRecipient; taxonomy: EquipmentTaxonomy | null }) {
  const unavailable = <span className="text-ink-faint">No registrado</span>;
  return (
    <>
      <Section title="Envío">
        <dl className="grid grid-cols-[9rem_minmax(0,1fr)] gap-x-2 gap-y-1.5 text-xs">
          <dt className="text-ink-faint">Dirección</dt>
          <dd className="break-all font-mono text-ink">{r.address}</dd>
          <dt className="text-ink-faint">Institución</dt>
          <dd className="text-ink">{r.organization_name ?? <span className="text-ink-faint">Sin institución vinculada</span>}</dd>
          <dt className="text-ink-faint">Resultado</dt>
          <dd>
            <Badge tone={OUTCOME[r.outcome].tone}>{OUTCOME[r.outcome].label}</Badge>
          </dd>
          <dt className="text-ink-faint">Enviado</dt>
          <dd className="text-ink">{r.sent_at ? fmtSent(r.sent_at) : unavailable}</dd>
          <dt className="text-ink-faint">Intentos</dt>
          <dd className="tabular-nums text-ink">
            {r.attempts} · {r.accepted_attempts} aceptado(s) por Gmail{r.rejected_attempts ? ` · ${r.rejected_attempts} rechazado(s)` : ""}
          </dd>
          <dt className="text-ink-faint">Entrega</dt>
          <dd className="text-ink">
            {r.bounce ? "Rebote registrado" : r.delivery_confirmed ? "Confirmada" : r.accepted_attempts ? "Sin confirmación de entrega" : "—"}
          </dd>
          {r.rejection ? (
            <>
              <dt className="text-ink-faint">Rechazo</dt>
              <dd className="text-ink">{REJECTION[r.rejection] ?? r.rejection}</dd>
            </>
          ) : null}
          {r.exclusion_reasons.length ? (
            <>
              <dt className="text-ink-faint">Exclusión</dt>
              <dd className="text-ink">{r.exclusion_reasons.map((x) => x.label).join(" · ")}</dd>
            </>
          ) : null}
          <dt className="text-ink-faint">BAJA</dt>
          <dd className="text-ink">
            {r.baja === "registered" ? "BAJA registrada para esta dirección" : r.baja === "pending_review" ? "BAJA en revisión" : "Ninguna registrada"}
          </dd>
          <dt className="text-ink-faint">Respuestas</dt>
          <dd className="text-ink">{r.replies ? `${r.replies} guardada(s)` : "Ninguna guardada en el CRM"}</dd>
          {r.gmail_url ? (
            <>
              <dt className="text-ink-faint">Gmail</dt>
              <dd>
                <ExternalLink href={r.gmail_url}>Abrir el mensaje enviado</ExternalLink>
              </dd>
            </>
          ) : null}
        </dl>
      </Section>
      <Section title="Interés en equipos">
        {r.interests && r.interests.length && taxonomy ? (
          <InterestList r={r} taxonomy={taxonomy} />
        ) : (
          <p className="text-xs text-ink-muted">Sin interés registrado para esta dirección. No significa bajo interés.</p>
        )}
      </Section>
    </>
  );
}

function InterestList({ r, taxonomy }: { r: HistoryRecipient; taxonomy: EquipmentTaxonomy }) {
  const label = useInterestLabel(taxonomy);
  const interests = useMemo(() => r.interests ?? [], [r.interests]);
  return <EvidenceList interests={interests} label={label} />;
}
