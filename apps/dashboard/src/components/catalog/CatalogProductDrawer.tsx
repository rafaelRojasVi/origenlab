import type { ReactNode } from "react";
import type { CatalogProductDetailUi } from "../../api/catalogTypes";
import {
  catalogConfidenceLabel,
  catalogEquipmentClassLabel,
  catalogProductKindLabel,
  catalogWebsiteHref,
  formatCatalogDate,
  formatCatalogMoney,
  formatCatalogQuantity,
  formatCommercialHistoryClientSale,
  formatCommercialHistoryDealState,
  formatCommercialLinkRef,
  groupCommercialHistoryByDeal,
  groupCatalogSpecs,
  primaryCategoryLabel,
  supplierPriceVisibilityLabel,
} from "../../lib/catalogFormat";

function DetailSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      {children}
    </section>
  );
}

export function CatalogProductDrawer({
  product,
  loading,
  error,
  open,
  onClose,
}: {
  product: CatalogProductDetailUi | null;
  loading: boolean;
  error: string | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!open) {
    return null;
  }

  const specGroups = product ? groupCatalogSpecs(product.specs) : [];
  const websiteHref = catalogWebsiteHref(product?.website_slug);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/30"
        aria-label="Cerrar ficha del producto"
        onClick={onClose}
      />
      <aside
        className="relative z-10 flex h-full w-full max-w-2xl flex-col border-l border-[var(--color-border)] bg-[var(--color-card)] shadow-xl"
        role="dialog"
        aria-labelledby="catalog-product-heading"
        aria-modal="true"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">
              Ficha del producto · solo lectura
            </p>
            <h2 id="catalog-product-heading" className="mt-1 text-lg font-semibold text-slate-900">
              {product?.display_name ?? "Cargando producto…"}
            </h2>
            {product ? (
              <p className="mt-1 text-xs text-[var(--color-muted)]">{product.product_key}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cerrar
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
          {loading ? (
            <p className="text-sm text-[var(--color-muted)]" role="status">
              Cargando ficha del producto…
            </p>
          ) : null}

          {error ? (
            <div
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
              role="alert"
            >
              {error}
            </div>
          ) : null}

          {product ? (
            <>
              <div className="flex flex-wrap gap-2">
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-950">
                  {catalogConfidenceLabel(product.confidence)}
                </span>
                {product.brand ? (
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                    {product.brand}
                  </span>
                ) : null}
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                  {primaryCategoryLabel(product)}
                </span>
              </div>

              <DetailSection title="Resumen">
                <p className="text-sm text-slate-800">
                  {product.public_summary?.trim() || "Sin resumen público registrado."}
                </p>
                <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-[var(--color-muted)]">Tipo</dt>
                    <dd className="font-medium">{catalogProductKindLabel(product.product_kind)}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Clase de equipo</dt>
                    <dd className="font-medium">
                      {catalogEquipmentClassLabel(product.equipment_class)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Modelo</dt>
                    <dd className="font-medium">{product.model_number?.trim() || "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Confianza</dt>
                    <dd className="font-medium">{catalogConfidenceLabel(product.confidence)}</dd>
                  </div>
                  {product.manufacturer_name ? (
                    <div className="sm:col-span-2">
                      <dt className="text-[var(--color-muted)]">Fabricante</dt>
                      <dd className="font-medium">{product.manufacturer_name}</dd>
                    </div>
                  ) : null}
                </dl>
              </DetailSection>

              <DetailSection title="Ficha técnica">
                {websiteHref ? (
                  <p className="mb-2">
                    <a
                      href={websiteHref}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium text-brand-700 hover:underline"
                    >
                      Ver ficha web
                    </a>
                  </p>
                ) : null}
                {specGroups.length === 0 ? (
                  <p className="text-sm text-[var(--color-muted)]">
                    Sin ficha técnica estructurada todavía.
                  </p>
                ) : (
                  <div className="space-y-4">
                    {specGroups.map((group) => (
                      <div key={group.label}>
                        <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                          {group.label}
                        </p>
                        <ul className="mt-1 space-y-1">
                          {group.items.map((spec) => (
                            <li
                              key={`${spec.spec_group ?? "g"}-${spec.spec_key}`}
                              className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
                            >
                              <span className="font-medium text-slate-800">{spec.spec_value}</span>
                              <span className="ml-2 text-xs text-[var(--color-muted)]">
                                {spec.spec_key}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </DetailSection>

              <DetailSection title="Alias / códigos">
                {product.aliases.length === 0 ? (
                  <p className="text-sm text-[var(--color-muted)]">Sin alias registrados.</p>
                ) : (
                  <ul className="divide-y divide-[var(--color-border)] rounded-lg border border-[var(--color-border)]">
                    {product.aliases.map((alias) => (
                      <li
                        key={`${alias.alias_source}-${alias.alias_code}`}
                        className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
                      >
                        <span className="font-mono text-slate-800">{alias.alias_code}</span>
                        <span className="text-[var(--color-muted)]">
                          {alias.alias_source}
                          {alias.alias_kind ? ` · ${alias.alias_kind}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </DetailSection>

              <DetailSection title="Ofertas de proveedor">
                {product.supplier_offers.length === 0 ? (
                  <p className="text-sm text-[var(--color-muted)]">
                    {product.commercial_history.length > 0
                      ? "Sin cotizaciones abiertas de proveedor. Este producto tiene historial comercial confirmado."
                      : "Sin ofertas de proveedor registradas."}
                  </p>
                ) : (
                  <ul className="space-y-3">
                    {product.supplier_offers.map((offer) => (
                      <li
                        key={offer.offer_key}
                        className="rounded-lg border border-[var(--color-border)] bg-slate-50 px-3 py-3 text-sm"
                      >
                        <p className="font-medium text-slate-900">
                          {offer.supplier_org_name?.trim() || "Proveedor sin nombre"}
                        </p>
                        <p className="text-xs text-[var(--color-muted)]">
                          Estado: {offer.offer_status}
                          {offer.incoterm ? ` · Incoterm: ${offer.incoterm}` : ""}
                        </p>
                        {offer.quantity_offered ? (
                          <p className="mt-1 text-slate-700">
                            Cantidad ofertada:{" "}
                            {formatCatalogQuantity(offer.quantity_offered, product.default_unit)}
                          </p>
                        ) : null}
                        {offer.quoted_at ? (
                          <p className="text-xs text-[var(--color-muted)]">
                            Cotizada: {formatCatalogDate(offer.quoted_at)}
                          </p>
                        ) : null}
                        {offer.valid_until ? (
                          <p className="text-xs text-[var(--color-muted)]">
                            {formatCatalogDate(offer.valid_until) ?? `Validez: ${offer.valid_until}`}
                          </p>
                        ) : null}
                        {offer.availability_note ? (
                          <p className="mt-2 text-slate-700">{offer.availability_note}</p>
                        ) : null}
                        {offer.payment_terms ? (
                          <p className="mt-1 text-xs text-[var(--color-muted)]">
                            Pago: {offer.payment_terms}
                          </p>
                        ) : null}
                        {offer.delivery_terms ? (
                          <p className="mt-1 text-xs text-[var(--color-muted)]">
                            Entrega: {offer.delivery_terms}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </DetailSection>

              <DetailSection title="Historial de precios">
                {product.price_snapshots.length === 0 ? (
                  <p className="text-sm text-[var(--color-muted)]">Sin historial de precios.</p>
                ) : (
                  <ul className="space-y-3">
                    {product.price_snapshots.map((snap) => (
                      <li
                        key={snap.snapshot_key}
                        className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-3 text-sm"
                      >
                        <p className="font-semibold text-amber-950">
                          {formatCatalogMoney(snap.amount_decimal, snap.currency)}
                        </p>
                        <p className="text-xs font-medium text-amber-900">
                          {supplierPriceVisibilityLabel(snap.is_public_safe)}
                        </p>
                        {product.product_key === "crtop-olt-hp-5l" ? (
                          <ul className="mt-2 list-inside list-disc text-slate-800">
                            <li>
                              Cantidad ofertada:{" "}
                              {formatCatalogQuantity(snap.quantity, snap.unit)}
                            </li>
                            <li>
                              Precio proveedor:{" "}
                              {formatCatalogMoney(snap.amount_decimal, snap.currency)}
                            </li>
                            {snap.incoterm ? <li>Incoterm: {snap.incoterm}</li> : null}
                          </ul>
                        ) : null}
                        {product.product_key === "ika-rv10-70-vapor-tube" ? (
                          <ul className="mt-2 list-inside list-disc text-slate-800">
                            <li>
                              Solicitud cliente:{" "}
                              {formatCatalogQuantity(snap.quantity, snap.unit)}
                            </li>
                            <li>
                              Monto {formatCatalogMoney(snap.amount_decimal, null)} registrado como
                              posible precio unitario
                            </li>
                            <li>Moneda pendiente</li>
                          </ul>
                        ) : null}
                        {product.product_key !== "crtop-olt-hp-5l" &&
                        product.product_key !== "ika-rv10-70-vapor-tube" ? (
                          <p className="mt-1 text-xs text-amber-900/80">
                            {snap.currency ? formatCatalogMoney(snap.amount_decimal, snap.currency) : "Moneda pendiente"}
                            {snap.incoterm ? ` · Incoterm: ${snap.incoterm}` : ""}
                            {snap.quantity
                              ? ` · ${formatCatalogQuantity(snap.quantity, snap.unit)}`
                              : ""}
                          </p>
                        ) : null}
                        {snap.price_notes ? (
                          <p className="mt-2 text-slate-800">{snap.price_notes}</p>
                        ) : null}
                        <p className="mt-1 text-xs text-[var(--color-muted)]">
                          {catalogConfidenceLabel(snap.confidence)}
                          {snap.observed_at
                            ? ` · ${formatCatalogDate(snap.observed_at) ?? ""}`
                            : ""}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </DetailSection>

              <DetailSection title="Historial comercial">
                {product.commercial_history.length === 0 ? (
                  <p className="text-sm text-[var(--color-muted)]">
                    Sin historial comercial registrado para este producto.
                  </p>
                ) : (
                  <ul className="space-y-4">
                    {groupCommercialHistoryByDeal(product.commercial_history).map((deal) => {
                      const dealState = formatCommercialHistoryDealState(deal.lines);
                      return (
                        <li
                          key={deal.dealKey}
                          className="rounded-lg border border-[var(--color-border)] bg-slate-50 px-3 py-3"
                        >
                          <p className="text-sm text-slate-800">
                            <span className="font-medium">Negocio vinculado:</span> {deal.dealLabel}
                          </p>
                          {dealState ? (
                            <p className="mt-1 text-sm text-slate-700">
                              <span className="font-medium">Estado:</span> {dealState}
                            </p>
                          ) : null}
                          <ul className="mt-3 space-y-3">
                            {deal.lines
                              .filter((line) => line.line_kind === "product")
                              .map((line) => (
                                <li
                                  key={line.history_key}
                                  className={
                                    line.line_side === "supplier"
                                      ? "rounded-md border border-amber-200 bg-amber-50/70 px-3 py-2 text-sm"
                                      : "rounded-md border border-slate-200 bg-white px-3 py-2 text-sm"
                                  }
                                >
                                  {line.line_side === "client" ? (
                                    <p className="text-slate-800">
                                      <span className="font-medium">Vendido a cliente:</span>{" "}
                                      {formatCommercialHistoryClientSale(line)}
                                    </p>
                                  ) : (
                                    <>
                                      <p className="text-slate-800">
                                        <span className="font-medium">Costo proveedor:</span>{" "}
                                        {formatCatalogMoney(line.amount_decimal, line.currency)}
                                      </p>
                                      <p className="mt-1 text-xs font-medium text-amber-900">
                                        {supplierPriceVisibilityLabel(line.is_public_safe)}
                                      </p>
                                    </>
                                  )}
                                  {line.quantity ? (
                                    <p className="mt-1 text-slate-700">
                                      {formatCatalogQuantity(line.quantity, line.unit)}
                                    </p>
                                  ) : null}
                                  {line.source_summary ? (
                                    <p className="mt-1 text-xs text-[var(--color-muted)]">
                                      {line.source_summary}
                                    </p>
                                  ) : null}
                                </li>
                              ))}
                          </ul>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </DetailSection>

              <DetailSection title="Negocios y casos vinculados">
                {product.commercial_links.length === 0 ? (
                  <p className="text-sm text-[var(--color-muted)]">Sin vínculos comerciales.</p>
                ) : (
                  <ul className="space-y-2">
                    {product.commercial_links.map((link) => (
                      <li
                        key={`${link.link_kind}-${link.link_ref}`}
                        className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm"
                      >
                        <p className="font-medium text-slate-800">{formatCommercialLinkRef(link)}</p>
                        <p className="text-xs text-[var(--color-muted)]">
                          {link.link_kind} · {catalogConfidenceLabel(link.confidence)}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </DetailSection>
            </>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
