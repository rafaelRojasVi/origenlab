import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2EmptyState } from "../components/v2/V2EmptyState";

export function CotizacionesPage({ onOpenVentas }: { onOpenVentas: () => void }) {
  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Vista consolidada"
        subtitle="Vista consolidada de cotizaciones — próximamente."
      />
      <V2EmptyState
        title="Aún no hay una vista consolidada de cotizaciones"
        description="Por ahora cada cotización vive dentro de su oportunidad, en Ventas: abre la oportunidad y usa la sección Cotización del panel."
        action={
          <button
            type="button"
            onClick={onOpenVentas}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            Ir a Ventas
          </button>
        }
      />
    </div>
  );
}
