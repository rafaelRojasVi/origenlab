/**
 * «Cobrar» — Finanzas v3 second tab.
 *
 * Renders the existing PagosPage (invoices, SII CSV import, case linking, pay marking)
 * with the v3 Finanzas nav tabs at the top.
 *
 * The nav tabs are intentionally a thin wrapper — PagosPage already holds all the
 * collection logic so we don't duplicate it.
 */
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

export function FinanzasCobrarPage({ navigate }: { navigate: Navigate }) {
  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <h1 className="text-[17px] font-semibold tracking-tight">Finanzas</h1>
        <FinanzasTabs active="fin-cobrar" navigate={navigate} />
      </div>
      <PagosPage navigate={navigate} />
    </div>
  );
}
