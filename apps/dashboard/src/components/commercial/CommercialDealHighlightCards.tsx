import type {
  CommercialDealProductLineUi,
  CommercialDealUiRow,
  CommercialDealsListUi,
} from "../../api/commercialDealsTypes";
import {
  catalogProductHash,
  dealStatusLabel,
  enrichProductLineCatalogKeys,
  formatBlockersPreview,
  formatClp,
  formatMarginPct,
  formatProductLineLabel,
  formatUpdatedAt,
  marginStatusLabel,
  resolveDealProductLines,
} from "../../lib/commercialDealFormat";

function isCeafServaDeal(row: CommercialDealUiRow): boolean {
  const client = (row.client_org_name || "").toUpperCase();
  const supplier = (row.supplier_org_name || "").toUpperCase();
  return client.includes("CEAF") && supplier.includes("SERVA");
}

function DealProductLines({ lines }: { lines: CommercialDealProductLineUi[] }) {
  if (!lines.length) {
    return null;
  }
  return (
    <div className="mt-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
        Productos del negocio
      </p>
      <ul className="mt-2 space-y-1.5 text-sm text-slate-800">
        {lines.map((line, index) => {
          const label = formatProductLineLabel(line);
          const key = line.catalog_product_key;
          return (
            <li key={`${label}-${index}`}>
              {key ? (
                <a
                  href={catalogProductHash(key)}
                  className="font-medium text-brand-800 underline decoration-brand-300 underline-offset-2 hover:text-slate-900"
                >
                  {label}
                </a>
              ) : (
                <span>{label}</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function DealHighlightCard({ row }: { row: CommercialDealUiRow }) {
  const hasBlockers = (row.margin_blockers?.length ?? 0) > 0;
  const productLines = enrichProductLineCatalogKeys(
    resolveDealProductLines(row.product_lines, row.client_org_name, row.supplier_org_name),
  );

  return (
    <article
      className={`rounded-xl border px-5 py-4 shadow-sm ${
        hasBlockers
          ? "border-amber-300 bg-amber-50/80 ring-1 ring-amber-200"
          : "border-[var(--color-border)] bg-[var(--color-card)]"
      }`}
      data-testid="deal-highlight-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
            Negocio comercial
          </p>
          <h3 className="mt-1 text-xl font-semibold text-slate-900">
            {row.client_org_name || "—"}
            <span className="mx-2 font-normal text-[var(--color-muted)]">×</span>
            {row.supplier_org_name || "—"}
          </h3>
        </div>
        {hasBlockers ? (
          <span className="rounded-full bg-amber-200 px-3 py-1 text-xs font-semibold text-amber-950">
            Bloqueos de margen
          </span>
        ) : null}
      </div>

      <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-[var(--color-muted)]">Estado del negocio</dt>
          <dd className="font-medium text-slate-800" title={row.deal_status}>
            {dealStatusLabel(row.deal_status)}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Margen</dt>
          <dd className="font-medium text-slate-800" title={row.margin_status}>
            {marginStatusLabel(row.margin_status)} · {formatMarginPct(row.margin_pct)}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Venta cliente (bruto)</dt>
          <dd className="font-medium tabular-nums">{formatClp(row.client_sale_gross_clp)}</dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Pago recibido (cliente)</dt>
          <dd className="font-medium tabular-nums">
            {formatClp(row.client_payment_received_clp)}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Margen neto</dt>
          <dd className="font-medium tabular-nums">{formatClp(row.margin_net_clp)}</dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Proveedor pagado (resumen)</dt>
          <dd className="font-medium tabular-nums">
            {row.supplier_amount_paid_decimal ? `EUR ${row.supplier_amount_paid_decimal}` : "—"}
          </dd>
        </div>
      </dl>

      {isCeafServaDeal(row) || productLines.length > 0 ? (
        <DealProductLines lines={productLines} />
      ) : null}

      {hasBlockers ? (
        <p className="mt-3 rounded-md border border-amber-200 bg-white/70 px-3 py-2 text-sm text-amber-950">
          <span className="font-semibold">Bloqueos:</span> {formatBlockersPreview(row.margin_blockers)}
        </p>
      ) : null}

      <p className="mt-3 text-xs text-[var(--color-muted)]">
        Actualizado {formatUpdatedAt(row.updated_at)} · espejo redactado
      </p>
    </article>
  );
}

export function CommercialDealHighlightCards({ data }: { data: CommercialDealsListUi | null }) {
  const items = data?.items ?? [];
  if (!data?.table_available || items.length === 0) {
    return null;
  }

  const featured =
    items.find((row) => isCeafServaDeal(row)) ?? items[0];

  const rest = items.filter((row) => row !== featured);

  return (
    <section className="space-y-4" aria-labelledby="deal-cards-heading">
      <h2 id="deal-cards-heading" className="text-lg font-semibold text-slate-900">
        Negocio destacado
      </h2>
      <DealHighlightCard row={featured} />
      {rest.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {rest.slice(0, 4).map((row, index) => (
            <DealHighlightCard key={`${row.client_org_name}-${row.supplier_org_name}-${index}`} row={row} />
          ))}
        </div>
      ) : null}
    </section>
  );
}
