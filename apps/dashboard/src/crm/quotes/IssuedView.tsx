/**
 * The Cotizador's read-only side: an issued revision (approved, sent, void, or a historical one
 * loaded from the mail) as approval froze it, the summary of an older revision, and the list of
 * revisions to choose from.
 *
 * Every number is the API's: the stored line prices and totals of the revision, only formatted.
 * The client's side (institution, recipient, conditions, validity, who approved it) comes from the
 * revision's `party_snapshot`; a historical revision may have none, and then only its lines show.
 * The workspace carries the full detail of the current revision only; an older one is shown from
 * its entry in `revisions` (number, status, total, dates), and the page says so.
 */
import { useId, type ReactNode } from "react";
import { fmtDay } from "../catalog/catalogApi";
import { formatMoney } from "../money";
import { Badge } from "../ui";
import { CostStrip } from "./CostStrip";
import type { LineEconomics, QuoteStoredLine, QuoteWorkspace, QuoteWorkspaceRevision } from "./quoteApi";
import { quoteLabel } from "./quoteLabel";

export const REVISION_STATUS: Record<string, { label: string; tone: "warn" | "good" | "info" | "neutral" }> = {
  draft: { label: "Borrador", tone: "warn" },
  in_review: { label: "En revisión", tone: "warn" },
  approved: { label: "Aprobada", tone: "good" },
  sent: { label: "Enviada", tone: "info" },
  void: { label: "Anulada", tone: "neutral" },
};

export const statusOf = (status: string | null | undefined) => REVISION_STATUS[status ?? "draft"] ?? { label: status ?? "", tone: "neutral" as const };

/** «CN00042A» for a revision of the quote, by its number. */
export function revisionLabel(workspace: QuoteWorkspace, revisionNo: number): string {
  const known = workspace.revisions.find((r) => r.revision_no === revisionNo);
  return quoteLabel({ label: known?.label, quote_number: workspace.quote.quote_number, revision_no: revisionNo });
}

/* ── the frozen client side ───────────────────────────────────────────────── */

export interface PartySnapshot {
  institution: { name: string | null; rut: string | null } | null;
  recipient: { name: string | null; email: string | null } | null;
  /** The rest of «Datos del cliente» as approved (cargo, unidad, teléfono, dirección, ciudad). */
  client: ClientLines;
  terms: Record<string, unknown>;
  extra: { label_es: string; text_es: string }[];
  validityDays: number | null;
  approvedBy: string | null;
  approvedAt: string | null;
  validUntil: string | null;
}

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

/** The snapshot as the API froze it, read defensively (a historical revision has none). */
export interface ClientLines {
  contactRole: string | null;
  department: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
}

/** «Datos del cliente» beyond institution and recipient, from a snapshot's `client` or a draft's `client`. */
export function clientLines(raw: unknown): ClientLines {
  const c = obj(raw) ?? {};
  return { contactRole: text(c.contact_role), department: text(c.department), phone: text(c.phone),
           address: text(c.address), city: text(c.city) };
}

export function readSnapshot(raw: unknown): PartySnapshot | null {
  const s = obj(raw);
  if (!s) return null;
  const inst = obj(s.institution);
  const rec = obj(s.recipient);
  const terms = obj(s.terms) ?? {};
  const extra = Array.isArray(terms.terms_extra)
    ? terms.terms_extra.flatMap((t) => {
        const o = obj(t);
        return o && text(o.label_es) && text(o.text_es) ? [{ label_es: String(o.label_es), text_es: String(o.text_es) }] : [];
      })
    : [];
  return {
    institution: inst ? { name: text(inst.name), rut: text(inst.rut) } : null,
    recipient: rec ? { name: text(rec.name), email: text(rec.email) } : null,
    client: clientLines(s.client),
    terms,
    extra,
    validityDays: typeof terms.validity_days === "number" ? terms.validity_days : null,
    approvedBy: text(s.approved_by),
    approvedAt: text(s.approved_at),
    validUntil: text(s.valid_until),
  };
}

/** The printed conditions, in the order the PDF prints them (the API's `PRINTED_TERMS`). */
export const PRINTED_TERMS: [string, string][] = [
  ["delivery_terms", "Plazo de entrega"],
  ["payment_terms", "Forma de pago"],
  ["warranty_terms_es", "Garantía"],
  ["delivery_place_es", "Lugar de entrega"],
  ["installation_terms_es", "Instalación y capacitación"],
  ["client_notes_es", "Nota para el cliente"],
];

const CARD = "flex flex-col gap-3 rounded-xl border border-line bg-canvas-raised px-5 py-4";

function Dates({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-ink-muted">
      {items.map((t) => (
        <li key={t}>{t}</li>
      ))}
    </ul>
  );
}

/** What is said about an issued revision's life: validity, approval, sending, replacement. */
function lifeLines(
  workspace: QuoteWorkspace,
  r: { approved_at: string | null; sent_at: string | null; superseded_by_revision_no: number | null; valid_until?: string | null },
  snapshot: PartySnapshot | null,
): string[] {
  const out: string[] = [];
  const validUntil = snapshot?.validUntil ?? r.valid_until ?? null;
  if (validUntil) out.push(`Válida hasta el ${fmtDay(validUntil)}`);
  const approvedAt = snapshot?.approvedAt ?? r.approved_at;
  if (approvedAt) {
    out.push(snapshot?.approvedBy ? `Aprobada por ${snapshot.approvedBy} el ${fmtDay(approvedAt)}` : `Aprobada el ${fmtDay(approvedAt)}`);
  }
  if (r.sent_at) out.push(`Enviada el ${fmtDay(r.sent_at)}`);
  if (r.superseded_by_revision_no != null) out.push(`Reemplazada por ${revisionLabel(workspace, r.superseded_by_revision_no)}`);
  return out;
}

/* ── the current issued revision ──────────────────────────────────────────── */

export function IssuedView({ workspace }: { workspace: QuoteWorkspace }) {
  const cur = workspace.current;
  // Unique per pane: the Cotizador module keeps several quotes in the page at once.
  const ids = useId();
  if (!cur) return null;
  const snapshot = readSnapshot(cur.party_snapshot);
  const currency = cur.quote_currency ?? "CLP";
  // Expense lines are internal (they are spread over the equipment): the client sees the items.
  const items = (cur.lines ?? []).filter((l) => l.line_kind === "item");
  const institution = snapshot?.institution ?? (workspace.case.institution ? { name: workspace.case.institution.name, rut: workspace.case.institution.rut } : null);
  const fixed = PRINTED_TERMS.flatMap(([key, label]) => {
    const v = text(snapshot?.terms[key]);
    return v ? [{ label, value: v }] : [];
  });
  const life = lifeLines(workspace, cur, snapshot);
  return (
    <div className="flex min-w-0 flex-[999_1_760px] flex-col gap-[18px]">
      {life.length ? (
        <div className="rounded-xl border border-line bg-canvas-sunken/60 px-5 py-3">
          <Dates items={life} />
        </div>
      ) : null}
      {institution || snapshot?.recipient ? (
        <section aria-labelledby={`${ids}-cliente`} className={CARD}>
          <h2 id={`${ids}-cliente`} className="text-[17px] font-semibold text-ink">
            Cliente
          </h2>
          <dl className="grid grid-cols-1 gap-3 text-[13.5px] md:grid-cols-2">
            {institution ? (
              <div>
                <dt className="text-[12.5px] text-ink-muted">Institución</dt>
                <dd className="font-medium text-ink">{institution.name ?? "—"}</dd>
                <dd className="text-[12.5px] text-ink-muted">{institution.rut ?? "RUT pendiente"}</dd>
              </div>
            ) : null}
            {snapshot?.recipient ? (
              <div>
                <dt className="text-[12.5px] text-ink-muted">Para</dt>
                {snapshot.recipient.name ? <dd className="font-medium text-ink">{snapshot.recipient.name}</dd> : null}
                {snapshot.recipient.email ? <dd className="break-all text-[12.5px] text-ink-muted">{snapshot.recipient.email}</dd> : null}
              </div>
            ) : null}
          </dl>
        </section>
      ) : null}
      <section aria-labelledby={`${ids}-productos`} className="overflow-hidden rounded-xl border border-line bg-canvas-raised">
        <h2 id={`${ids}-productos`} className="px-5 pb-3 pt-4 text-[17px] font-semibold text-ink">
          Productos
        </h2>
        <StoredLines lines={items} currency={currency} economics={cur.line_economics ?? null} />
      </section>
      {fixed.length || snapshot?.validityDays != null || snapshot?.extra.length ? (
        <section aria-labelledby={`${ids}-condiciones`} className={CARD}>
          <h2 id={`${ids}-condiciones`} className="text-[17px] font-semibold text-ink">
            Condiciones
          </h2>
          <dl className="grid grid-cols-1 gap-3 text-[13.5px] md:grid-cols-2">
            {fixed.slice(0, 1).map((t) => (
              <Term key={t.label} label={t.label} value={t.value} />
            ))}
            {snapshot?.validityDays != null ? (
              <Term label="Validez" value={`${snapshot.validityDays} ${snapshot.validityDays === 1 ? "día" : "días"}`} />
            ) : null}
            {fixed.slice(1).map((t) => (
              <Term key={t.label} label={t.label} value={t.value} />
            ))}
            {snapshot?.extra.map((t, i) => (
              <Term key={`extra-${i}`} label={t.label_es} value={t.text_es} />
            ))}
          </dl>
        </section>
      ) : null}
    </div>
  );
}

function Term({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12.5px] text-ink-muted">{label}</dt>
      <dd className="whitespace-pre-line break-words text-ink">{value}</dd>
    </div>
  );
}

const GRID = "grid grid-cols-[minmax(240px,1fr)_80px_130px_140px] gap-3";

/** The approved lines; under each, the cost strip as approval froze it (sales and admin only, as the whole page). */
function StoredLines({ lines, currency, economics }: { lines: QuoteStoredLine[]; currency: string; economics: LineEconomics[] | null }) {
  const money = (v: string | null) => formatMoney(v, currency) ?? "—";
  // A stored quantity reads «2», not «2.000000»: trailing zeros dropped, decimal comma; no arithmetic.
  const qty = (v: string | null) => (v == null ? "—" : v.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "").replace(".", ","));
  return (
    <div className="overflow-x-auto">
      <div role="table" aria-label="Líneas aprobadas" className="min-w-[640px]">
        <div role="row" className={`${GRID} border-y border-line/80 bg-canvas-sunken px-5 py-2 text-xs font-semibold text-ink-muted`}>
          <span role="columnheader">Producto</span>
          <span role="columnheader" className="text-right">
            Cant.
          </span>
          <span role="columnheader" className="text-right">
            Precio unit.
          </span>
          <span role="columnheader" className="text-right">
            Total
          </span>
        </div>
        {lines.length === 0 ? (
          <p className="px-5 py-6 text-[13px] text-ink-muted">Esta revisión no tiene líneas guardadas.</p>
        ) : (
          lines.map((l, i) => {
            const strip = economics?.find((e) => e.line_no === l.line_no) ?? null;
            return (
            <div key={l.line_no} className="border-b border-line/60 px-5 py-3 last:border-b-0">
            <div role="row" className={`${GRID} items-start text-[13.5px]`}>
              <span role="cell" className="min-w-0">
                <span className="block break-words text-ink">{l.description ?? "—"}</span>
                {l.line_note_es ? <span className="mt-0.5 block text-[12.5px] text-ink-muted">{l.line_note_es}</span> : null}
              </span>
              <span role="cell" className="text-right font-mono tabular-nums text-ink">
                {qty(l.quantity)}
              </span>
              <span role="cell" className="text-right font-mono tabular-nums text-ink">
                {money(l.unit_price_qc)}
              </span>
              <span role="cell" className="text-right font-mono tabular-nums font-medium text-ink">
                {money(l.line_total_qc)}
              </span>
            </div>
            {strip ? <CostStrip economics={strip} n={i + 1} /> : null}
            </div>
            );
          })
        )}
      </div>
    </div>
  );
}

/* ── an older revision: its summary only ──────────────────────────────────── */

export function RevisionSummary({
  workspace,
  revision,
  actions,
}: {
  workspace: QuoteWorkspace;
  revision: QuoteWorkspaceRevision;
  actions?: ReactNode;
}) {
  const label = quoteLabel({ label: revision.label, quote_number: workspace.quote.quote_number, revision_no: revision.revision_no });
  const badge = statusOf(revision.status);
  const life = lifeLines(workspace, revision, null);
  const currency = workspace.current?.quote_currency ?? "CLP";
  return (
    <section aria-label={`Revisión ${label}`} className={`${CARD} min-w-0 flex-[999_1_760px]`}>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[17px] font-semibold text-ink">
          Revisión <span className="font-mono font-medium tabular-nums">{label}</span>
        </h2>
        <Badge tone={badge.tone}>{badge.label}</Badge>
        {revision.origin !== "authored" ? <span className="text-[12.5px] text-ink-muted">histórica</span> : null}
        {actions ? <div className="ml-auto flex flex-wrap gap-2">{actions}</div> : null}
      </div>
      <dl className="text-[13.5px]">
        <dt className="text-[12.5px] text-ink-muted">Total</dt>
        <dd className="font-mono text-[20px] font-medium tabular-nums text-ink">{formatMoney(revision.grand_total, currency) ?? "—"}</dd>
      </dl>
      <Dates items={life} />
      <p className="text-[12.5px] text-ink-muted">
        De las revisiones anteriores se muestra sólo su resumen; el detalle (líneas y condiciones) es el de la revisión actual.
      </p>
    </section>
  );
}

/* ── the list of revisions ────────────────────────────────────────────────── */

export function RevisionSwitcher({
  workspace,
  selectedId,
  onSelect,
}: {
  workspace: QuoteWorkspace;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (workspace.revisions.length < 2) return null;
  return (
    <nav aria-label="Revisiones" className="flex flex-wrap items-center gap-2">
      <span className="text-[12.5px] font-medium text-ink-muted">Revisiones:</span>
      {workspace.revisions.map((r) => {
        const label = quoteLabel({ label: r.label, quote_number: workspace.quote.quote_number, revision_no: r.revision_no });
        const status = statusOf(r.status).label;
        const selected = r.id === selectedId;
        return (
          <button
            key={r.id}
            type="button"
            aria-pressed={selected}
            aria-label={`${label} · ${status}${r.id === workspace.current?.id ? " (actual)" : ""}`}
            onClick={() => onSelect(r.id)}
            className={`inline-flex min-h-[36px] items-center gap-2 rounded-full border px-3 text-[12.5px] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
              selected ? "border-brand-700 bg-brand-700/10 font-semibold text-brand-700" : "border-line bg-canvas-raised text-ink hover:bg-canvas-sunken"
            }`}
          >
            <span className="font-mono tabular-nums">{label}</span>
            <span className="text-ink-muted">{status}</span>
          </button>
        );
      })}
    </nav>
  );
}
