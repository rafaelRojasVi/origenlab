/**
 * «Resumen» — Finanzas v3 main tab.
 *
 * Sections (from canvas Finanzas-Resumen.dc.html):
 *  1. Caja a 30 · 60 · 90 días
 *  2. ¿Llegamos a la meta? (3-layer stacked bar: facturado / ganado / negociación)
 *  3. Indicadores (días de cobro · margen real · clientes lentos)
 *  4. ¿A quién cobrar hoy? (oldest 1–3 open invoices)
 *  5. Socios (admin only, collapsed)
 */
import { useState } from "react";
import { crmHash } from "../crmRoute";
import { formatMoney } from "../money";
import { ResourceGate, Skeleton, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";
import type { CajaHorizon, CobrarHoyItem, ResumenData } from "./financeApi";
import { fetchFinanceResumen, fetchFinanceSettings } from "./financeApi";
import type { CrmSection } from "../crmRoute";

type Navigate = (section: CrmSection, id?: string | null) => void;

const clp = (n: number) => formatMoney(String(Math.round(n)), "CLP") ?? "—";
const M = (n: number) => {
  const v = Math.abs(n) / 1_000_000;
  return `${n < 0 ? "−" : ""}$${v.toLocaleString("es-CL", { minimumFractionDigits: 1, maximumFractionDigits: 2 })} M`;
};

function mesLabel(mes: string): string {
  const MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
  const d = new Date(mes + "T12:00:00Z");
  return `${MONTHS[d.getUTCMonth()] ?? ""} ${d.getUTCFullYear()}`;
}

// ── FinanzasTabs ──────────────────────────────────────────────────────────────

function FinanzasTabs({ active, navigate }: { active: "fin-resumen" | "fin-cobrar" | "fin-pagar"; navigate: Navigate }) {
  const tabs: Array<{ id: "fin-resumen" | "fin-cobrar" | "fin-pagar"; label: string }> = [
    { id: "fin-resumen", label: "Resumen" },
    { id: "fin-cobrar", label: "Cobrar" },
    { id: "fin-pagar", label: "Pagar" },
  ];
  return (
    <nav className="inline-flex gap-0.5 rounded-lg bg-sunken p-0.5" aria-label="Pestañas de Finanzas">
      {tabs.map((t) => (
        <a
          key={t.id}
          href={crmHash(t.id)}
          onClick={(e) => { e.preventDefault(); navigate(t.id); }}
          aria-current={active === t.id ? "page" : undefined}
          className={[
            "inline-flex h-8 items-center rounded-md px-3.5 text-[13px] font-semibold transition-colors",
            active === t.id
              ? "bg-raised text-ink shadow-sm"
              : "text-ink-muted hover:text-ink",
          ].join(" ")}
        >
          {t.label}
        </a>
      ))}
    </nav>
  );
}

// ── CajaSection ───────────────────────────────────────────────────────────────

function CajaSection({ horizons }: { horizons: CajaHorizon[] }) {
  if (!horizons.length) return null;
  const h = horizons[0];
  const frase = `En 30 días entrarían ~${M(h.entra)} y saldrían ~${M(h.sale)}: quedarían ~${M(h.neto)} en caja.`;
  return (
    <section className="rounded-xl border border-line bg-raised p-4 flex flex-col gap-3" aria-labelledby="caja-h">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 id="caja-h" className="text-[15px] font-bold tracking-tight">Caja a 30 · 60 · 90 días</h2>
        <p className="text-xs text-ink-faint">
          Con IVA · acumulado desde hoy ·{" "}
          <a className="text-accent font-semibold hover:underline underline-offset-2" href={crmHash("fin-pagar")}>
            ver lo que sale en Pagar
          </a>
        </p>
      </div>
      <p className="text-[15px] text-ink">{frase}</p>
      <div className="grid grid-cols-3 gap-px bg-line rounded-xl overflow-hidden border border-line">
        {horizons.map((h2) => {
          const maxEntra = Math.max(...horizons.map((x) => x.entra), 1);
          const inH = Math.max(4, Math.round(h2.entra / maxEntra * 56));
          const outH = Math.max(4, Math.round(h2.sale / maxEntra * 56));
          return (
            <div key={h2.horizon} className="bg-raised px-4 py-3 grid grid-cols-[1fr_60px] grid-rows-3 gap-y-1 gap-x-3 items-end">
              <span className="text-xs font-bold text-ink-muted col-start-1">
                {h2.horizon} días <span className="font-normal text-ink-faint">· hasta el {fmtDate(h2.until)}</span>
              </span>
              <span
                className={`text-[26px] font-bold tracking-tight leading-none col-start-1 ${h2.neto < 0 ? "text-bad" : "text-ink"}`}
                aria-label={`Neto a ${h2.horizon} días`}
              >
                {clp(h2.neto)}
              </span>
              <div className="col-start-1 flex gap-3 text-xs text-ink-muted">
                <span>
                  <span className="inline-block w-2 h-2 rounded-sm bg-accent mr-1.5 align-middle" />
                  entra <strong className="text-ink">{M(h2.entra)}</strong>
                </span>
                <span>
                  <span className="inline-block w-2 h-2 rounded-sm bg-ink-faint mr-1.5 align-middle" />
                  sale <strong className="text-ink">{M(h2.sale)}</strong>
                </span>
              </div>
              <div
                className="col-start-2 row-start-1 row-span-3 flex items-end gap-1.5 h-14 self-end"
                aria-hidden="true"
              >
                <div className="flex-1 bg-accent rounded-t-sm" style={{ height: inH }} />
                <div className="flex-1 bg-ink-faint rounded-t-sm" style={{ height: outH }} />
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-ink-faint">
        Estimado: entra cada factura abierta en su fecha esperada de pago (emisión + días que suele tardar ese cliente; si no lo sabemos, 30) y lo ganado sin facturar cuando toque facturarlo (~60 d). Sale el costo de proveedor estimado. No cuenta lo que está en negociación.
      </p>
    </section>
  );
}

// ── MetaSection ───────────────────────────────────────────────────────────────

function MetaSection({ data }: { data: ResumenData["meta"] }) {
  const [hov, setHov] = useState<1 | 2 | 3 | null>(null);
  const { facturado_net: fac, ganado_sin_facturar_net: gsf, en_negociacion_net: neg,
    meta_net: meta, dias_habiles_restantes: dias, ritmo_necesario: ritmo, mes } = data;
  const faltan = meta - fac;
  const p1 = meta > 0 ? Math.min(fac / meta, 1) : 0;
  const p2 = meta > 0 ? Math.min(gsf / meta, 1 - p1) : 0;
  const p3 = meta > 0 ? Math.min(neg / meta, Math.max(0, 1 - p1 - p2)) : 0;
  const seguro = fac + gsf;
  const cob = meta > 0 ? ((fac + gsf + neg) / meta).toFixed(1).replace(".", ",") : "0";

  return (
    <section className="rounded-xl border border-line bg-raised p-4 flex flex-col gap-3" aria-labelledby="meta-h">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 id="meta-h" className="text-[15px] font-bold tracking-tight">¿Llegamos a la meta?</h2>
        <p className="text-xs text-ink-faint">
          Meta de {mesLabel(mes)} {clp(meta)} neto ·{" "}
          <a className="text-accent font-semibold hover:underline underline-offset-2" href={crmHash("ajustes-meta")}>
            Cambiar meta
          </a>
        </p>
      </div>
      <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] gap-4 items-start">
        <div className="flex flex-col gap-2.5">
          <div className="flex items-baseline gap-2.5 flex-wrap">
            <span className="text-[30px] font-bold tracking-tight leading-tight">{clp(fac)}</span>
            <span className="text-[13px] text-ink-muted">
              facturado neto ·{" "}
              <strong className="text-ink">{clp(seguro)}</strong> seguro contando lo ganado · meta {clp(meta)}
            </span>
          </div>
          {/* Stacked bar */}
          <div
            className="flex h-[22px] rounded-md overflow-hidden bg-sunken"
            role="img"
            aria-label={`Meta ${clp(meta)}: facturado ${clp(fac)}, ganado sin facturar ${clp(gsf)}, en negociación ${clp(neg)}`}
          >
            <button
              type="button"
              className="h-full bg-accent transition-[width] duration-700 hover:brightness-90 focus-visible:outline focus-visible:outline-2"
              style={{ width: `${p1 * 100}%` }}
              onMouseEnter={() => setHov(1)} onMouseLeave={() => setHov(null)}
              onFocus={() => setHov(1)} onBlur={() => setHov(null)}
              aria-label={`Facturado: ${clp(fac)}`}
            />
            <button
              type="button"
              className="h-full bg-accent-soft transition-[width] duration-700 hover:brightness-90 focus-visible:outline focus-visible:outline-2"
              style={{ width: `${p2 * 100}%` }}
              onMouseEnter={() => setHov(2)} onMouseLeave={() => setHov(null)}
              onFocus={() => setHov(2)} onBlur={() => setHov(null)}
              aria-label={`Ganado sin facturar: ${clp(gsf)}`}
            />
            <button
              type="button"
              className="h-full border-l border-line-strong"
              style={{ width: `${p3 * 100}%`, background: "repeating-linear-gradient(135deg, var(--color-line-strong) 0 3px, transparent 3px 7px)" }}
              onMouseEnter={() => setHov(3)} onMouseLeave={() => setHov(null)}
              onFocus={() => setHov(3)} onBlur={() => setHov(null)}
              aria-label={`En negociación: ${clp(neg)}`}
            />
          </div>
          {/* Legend */}
          <div className="flex gap-4 flex-wrap text-xs text-ink-muted">
            {([
              { i: 1 as const, bg: "bg-accent", label: "Facturado", val: fac, hint: hov === 1 ? `· ${data.ganado_sin_facturar_count === 0 ? "sin casos ganados" : ""}` : "" },
              { i: 2 as const, bg: "bg-accent-soft", label: "Ganado sin facturar", val: gsf, hint: hov === 2 ? `· ${data.ganado_sin_facturar_count} caso${data.ganado_sin_facturar_count !== 1 ? "s" : ""}` : "" },
              { i: 3 as const, bg: "", label: "En negociación", val: neg, hint: hov === 3 ? `· ${data.en_negociacion_count} casos · posible` : "" },
            ] as const).map((item) => (
              <span
                key={item.i}
                className={`inline-flex items-center gap-1.5 min-h-[22px] px-1.5 -mx-1.5 rounded-md transition-colors ${hov === item.i ? "bg-sunken text-ink" : ""}`}
              >
                {item.i === 3 ? (
                  <span className="w-2.5 h-2.5 rounded-sm border border-line-strong" style={{ background: "repeating-linear-gradient(135deg, var(--color-line-strong) 0 2px, transparent 2px 5px)" }} />
                ) : (
                  <span className={`w-2.5 h-2.5 rounded-sm ${item.bg}`} />
                )}
                {item.label} <strong className="text-ink">{clp(item.val)}</strong>
                {item.hint && <span className="text-ink-faint">{item.hint}</span>}
              </span>
            ))}
          </div>
          <p className="text-xs text-ink-faint">
            La negociación es «posible», no se suma a lo seguro; cubriría la meta {cob} veces si cerrara entera.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 content-start pt-1">
          {[
            { label: "Faltan", val: clp(faltan), sub: `o ${clp(Math.max(0, faltan - gsf))} si se factura lo ganado` },
            { label: "Ritmo necesario", val: clp(ritmo), sub: "neto por día hábil" },
            { label: "Días hábiles", val: String(dias), sub: `restantes de ${mesLabel(mes)}` },
            { label: "Negociando", val: String(data.en_negociacion_count), sub: <a className="text-accent font-semibold hover:underline underline-offset-2 text-xs" href={crmHash("oportunidades")}>Ver en Ventas › Pipeline</a> },
          ].map((item) => (
            <div key={item.label}>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{item.label}</div>
              <div className="text-[18px] font-semibold">{item.val}</div>
              <div className="text-xs text-ink-faint">{item.sub}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── IndicadoresSection ────────────────────────────────────────────────────────

function IndicadoresSection({ data }: { data: ResumenData["indicadores"] }) {
  const { dias_promedio_cobro: dpc, dias_promedio_cobro_count: cnt, margen_mes_pct: margen,
    margen_mes_costo: costo, margen_mes_subtotal: subtotal, clientes_lentos: lentos } = data;
  const dpcDiff = Math.round(dpc - 30);
  return (
    <div
      className="grid grid-cols-3 gap-px bg-line rounded-xl overflow-hidden border border-line"
      aria-label="Indicadores del mes"
    >
      <div className="bg-raised px-4 py-3 flex flex-col gap-1 min-h-[118px]">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Días promedio de cobro</span>
        <span className="text-[24px] font-bold tracking-tight leading-tight">
          {dpc > 0 ? dpc.toFixed(0) : "—"} <small className="text-[12px] font-medium text-ink-muted">días</small>
        </span>
        <span className="text-xs text-ink-muted">
          la ley fija <strong>30</strong>
          {dpc > 0 && dpcDiff > 0 && (
            <> · vamos <strong className="text-bad">{dpcDiff} días tarde</strong></>
          )}
          {cnt > 0 && <> · {cnt} factura{cnt !== 1 ? "s" : ""} pagada{cnt !== 1 ? "s" : ""}</>}
        </span>
        <span className="text-[11px] text-ink-faint mt-auto leading-snug">
          Desde ahora: las fechas de pago anteriores al 5 nov son de cuando se registraron.
        </span>
      </div>
      <div className="bg-raised px-4 py-3 flex flex-col gap-1 min-h-[118px]">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Margen real del mes</span>
        <span className="text-[24px] font-bold tracking-tight leading-tight">
          {subtotal > 0 ? `${margen.toFixed(0)} %` : "—"} <small className="text-[12px] font-medium text-ink-muted">del mes</small>
        </span>
        {subtotal > 0 && (
          <span className="text-xs text-ink-muted">
            {clp(subtotal)} neto · costo proveedor {clp(costo)}
          </span>
        )}
        <span className="text-[11px] text-ink-faint mt-auto">Costo de proveedor de cada cotización ganada.</span>
      </div>
      <div className="bg-raised px-4 py-3 flex flex-col gap-1 min-h-[118px]">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Clientes que pagan tarde</span>
        {lentos.length === 0 ? (
          <span className="text-xs text-ink-faint mt-2">Sin historial de pagos aún.</span>
        ) : (
          <div className="flex flex-col gap-1.5 text-xs mt-1">
            {lentos.map((c) => (
              <div key={c.org} className="grid grid-cols-[1fr_auto] gap-x-2 items-baseline">
                <strong className="text-[13px] font-semibold">{c.org}</strong>
                <span className="text-warn font-semibold whitespace-nowrap">{c.days} días</span>
                {c.hint && <span className="col-span-2 text-ink-faint">{c.hint}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── CobrarHoySection ──────────────────────────────────────────────────────────

function CobrarHoySection({ items, totalStr, navigate }: { items: CobrarHoyItem[]; totalStr: string; navigate: Navigate }) {
  const [reminded, setReminded] = useState<string[]>([]);

  if (items.length === 0) {
    return (
      <section className="rounded-xl border border-line bg-raised p-4 flex flex-col gap-2" aria-labelledby="cobrar-h">
        <h2 id="cobrar-h" className="text-[15px] font-bold tracking-tight">¿A quién cobrar hoy?</h2>
        <p className="text-sm text-ink-muted">Sin facturas por cobrar.</p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-line bg-raised p-4 flex flex-col gap-2" aria-labelledby="cobrar-h">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 id="cobrar-h" className="text-[15px] font-bold tracking-tight">¿A quién cobrar hoy?</h2>
        <p className="text-xs text-ink-faint">
          Nos deben <strong className="text-ink">{totalStr}</strong> con IVA en {items.length} factura{items.length !== 1 ? "s" : ""}
        </p>
      </div>
      <div className="flex flex-col">
        {items.map((inv) => {
          const isReminded = reminded.includes(inv.invoice_id);
          const isLate = inv.age_days > 30;
          return (
            <div key={inv.invoice_id} className="flex items-center gap-3 py-2.5 border-t border-line first:border-t-0">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <strong className="text-[14px] font-semibold">{inv.client_name}</strong>
                  <span className={[
                    "inline-flex items-center h-5 px-2 rounded-full text-[11px] font-semibold border",
                    inv.avg_days_client <= 30 ? "bg-good-bg text-good border-transparent" : "bg-warn-bg text-warn border-transparent",
                  ].join(" ")}>
                    paga a {inv.avg_days_client} d
                  </span>
                </div>
                <span className="block text-xs text-ink-faint mt-0.5">
                  Factura {inv.folio} · {fmtDate(inv.issued_on)} · pago esperado {fmtDate(inv.expected_pay_date)}
                  {inv.opportunity_title && (
                    <> · <a
                      className="text-accent font-semibold hover:underline underline-offset-2"
                      href={crmHash("oportunidades", inv.opportunity_id)}
                      onClick={(e) => { e.preventDefault(); if (inv.opportunity_id) navigate("oportunidades", inv.opportunity_id); }}
                    >{inv.opportunity_title}</a></>
                  )}
                </span>
                {isReminded && (
                  <span className="flex items-center gap-1.5 mt-1 text-xs text-good font-semibold">
                    <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3.5 8.5l3 3 6-7"/></svg>
                    Borrador creado en Gmail{isLate ? " · menciona la Ley 21.131" : ""}
                  </span>
                )}
              </div>
              <span className={[
                "inline-flex items-center h-5 px-2 rounded-full text-[11px] font-semibold border",
                isLate ? "bg-warn-bg text-warn border-transparent" : "bg-sunken text-ink-muted border-line",
              ].join(" ")}>
                {inv.age_days} d{isLate ? " · vencida" : ""}
              </span>
              <span className="text-[15px] font-semibold whitespace-nowrap">{clp(inv.balance_iva)}</span>
              <button
                type="button"
                onClick={() => { setReminded((r) => [...r, inv.invoice_id]); toast("Borrador creado en Gmail"); }}
                className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-line-strong bg-raised text-[13px] font-semibold whitespace-nowrap hover:bg-sunken transition-colors"
              >
                <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="4" width="12" height="9" rx="1"/><path d="M2 5l6 4 6-4"/></svg>
                Recordar pago
              </button>
            </div>
          );
        })}
      </div>
      <div className="flex justify-end border-t border-line pt-2.5">
        <a
          className="text-accent font-semibold text-[13px] hover:underline underline-offset-2"
          href={crmHash("fin-cobrar")}
          onClick={(e) => { e.preventDefault(); navigate("fin-cobrar"); }}
        >
          Ver todo en Cobrar →
        </a>
      </div>
    </section>
  );
}

// ── SociosSection ─────────────────────────────────────────────────────────────

function SociosSection({ mes, facturadoNet }: { mes: string; facturadoNet: number }) {
  const [settingsState] = useResource(fetchFinanceSettings);
  if (settingsState.kind === "loading") return <Skeleton rows={2} />;
  const settings = settingsState.kind === "ready" ? settingsState.data : null;
  const rule = settings?.partner_rule ?? null;

  return (
    <section className="rounded-xl border border-line bg-raised px-5 py-2.5" aria-labelledby="socios-h">
      <details>
        <summary className="list-none flex items-center gap-2 text-[13px] font-semibold text-ink-muted min-h-8 cursor-pointer [&::-webkit-details-marker]:hidden">
          <span className="text-sm font-bold text-ink">Lo que queda para socios</span>
          <span className="inline-flex items-center h-5 px-2 rounded-full bg-sunken text-ink-muted border border-line text-[11px] font-semibold">solo administración</span>
          <span className="ml-auto text-xs text-ink-muted">{mesLabel(mes)} · calculado sobre facturado y gastos registrados</span>
        </summary>
        <div className="pt-3 pb-1.5">
          {rule ? (
            <div className="grid grid-cols-2 gap-6">
              <div className="grid grid-cols-[1fr_auto] gap-y-1.5 gap-x-6 text-[13px] max-w-xs">
                <span>Facturado neto del mes</span><strong>{clp(facturadoNet)}</strong>
                <span className="font-semibold border-t border-line-strong pt-1.5">Regla: {rule.rule_name}</span>
                <span className="border-t border-line-strong pt-1.5 text-ink-faint text-xs">vigente desde {fmtDate(rule.valid_from)}</span>
              </div>
              <div className="flex flex-col gap-1.5 text-[13px]">
                <a
                  className="text-accent font-semibold text-xs hover:underline underline-offset-2"
                  href={crmHash("ajustes-meta")}
                >
                  Cambiar la regla en Ajustes › Meta y socios →
                </a>
                <p className="text-xs text-ink-faint">Se reparte lo facturado y cobrado, no lo ganado.</p>
              </div>
            </div>
          ) : (
            <p className="text-xs text-ink-faint">
              Sin regla configurada.{" "}
              <a className="text-accent font-semibold hover:underline underline-offset-2" href={crmHash("ajustes-meta")}>
                Configurar en Ajustes › Meta y socios
              </a>
            </p>
          )}
        </div>
      </details>
    </section>
  );
}

// ── FinanzasResumenPage ───────────────────────────────────────────────────────

export function FinanzasResumenPage({ navigate, admin }: { navigate: Navigate; admin: boolean }) {
  const [state, reload] = useResource(fetchFinanceResumen);

  return (
    <div className="flex flex-col gap-3.5 pb-10">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Finanzas</h1>
          {state.kind === "ready" && (
            <p className="mt-0.5 text-[13px] text-ink-muted">
              {mesLabel(state.data.meta.mes)} · cifras netas salvo donde dice «con IVA»
            </p>
          )}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <FinanzasTabs active="fin-resumen" navigate={navigate} />
        </div>
      </div>

      <ResourceGate state={state} reload={reload} errorTitle="No se pudo cargar Finanzas">
        {(data) => (
          <div className="flex flex-col gap-3.5">
            <CajaSection horizons={data.caja} />
            <MetaSection data={data.meta} />
            <IndicadoresSection data={data.indicadores} />
            <CobrarHoySection
              items={data.cobrar_hoy}
              totalStr={clp(data.cobrar_hoy.reduce((s, i) => s + i.balance_iva, 0))}
              navigate={navigate}
            />
            {admin && (
              <SociosSection mes={data.meta.mes} facturadoNet={data.meta.facturado_net} />
            )}
          </div>
        )}
      </ResourceGate>
    </div>
  );
}
