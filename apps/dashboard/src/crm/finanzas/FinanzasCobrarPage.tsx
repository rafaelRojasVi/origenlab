/**
 * «Cobrar» — Finanzas v3 second tab.
 *
 * Renders the existing PagosPage (invoices, SII CSV import, case linking, pay marking)
 * with the v3 Finanzas nav tabs at the top plus a «¿Cómo cobrar antes?» help popover.
 */
import { useState } from "react";
import { crmHash, type CrmSection } from "../crmRoute";
import { PagosPage } from "../pagos/PagosPage";

type Navigate = (section: CrmSection, id?: string | null) => void;

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

function ComoCobraAntesPanel() {
  return (
    <div className="rounded-xl border border-line bg-sunken px-4 py-3 text-xs text-ink-muted space-y-2.5">
      <p className="font-semibold text-ink text-[13px]">¿Cómo cobrar antes?</p>
      <p>
        <strong className="font-semibold text-ink">Ley 21.131 — Pago a 30 días.</strong>{" "}
        Desde el 1 de enero de 2020, las facturas a grandes empresas vencen en 30 días corridos.
        El incumplimiento da derecho a cobro de intereses y gastos de cobranza.
        Recuerda la fecha al cliente y pide confirmación de recepción.
      </p>
      <p>
        <strong className="font-semibold text-ink">50 % de anticipo.</strong>{" "}
        Incluye en la cotización una condición de pago con 50 % al aprobar y el resto contra entrega.
        Reduce el riesgo y mejora el flujo de caja sin necesitar financiamiento externo.
      </p>
      <p>
        <strong className="font-semibold text-ink">Factoring.</strong>{" "}
        Puedes ceder la factura a una empresa de factoring o a tu banco y recibir entre el 85 % y el
        95 % del monto inmediatamente. El costo habitual es 1 %–2 % mensual sobre el monto cedido.
        Usa el simulador de Financiamiento para estimar el costo exacto.
      </p>
      <p>
        <strong className="font-semibold text-ink">FOGAPE.</strong>{" "}
        El Fondo de Garantía para Pequeños Empresarios permite a PYMES acceder a crédito bancario con
        garantía estatal de hasta el 80 % del capital. Contacta tu banco para solicitar una línea
        FOGAPE si el capital de trabajo requerido supera la liquidez inmediata.
      </p>
    </div>
  );
}

export function FinanzasCobrarPage({ navigate }: { navigate: Navigate }) {
  const [showHelp, setShowHelp] = useState(false);
  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <h1 className="text-[17px] font-semibold tracking-tight">Finanzas</h1>
          <button
            type="button"
            aria-label="¿Cómo cobrar antes?"
            aria-expanded={showHelp}
            onClick={() => setShowHelp((v) => !v)}
            className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-line text-[11px] font-bold text-ink-muted hover:border-line-strong hover:text-ink transition-colors"
          >
            ?
          </button>
        </div>
        <FinanzasTabs active="fin-cobrar" navigate={navigate} />
      </div>
      {showHelp && <ComoCobraAntesPanel />}
      <PagosPage navigate={navigate} />
    </div>
  );
}
