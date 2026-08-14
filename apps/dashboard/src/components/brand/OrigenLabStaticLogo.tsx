/** Marca estática para sidebar (sin canvas, sin icono). */
export function OrigenLabStaticLogo({ compact = false }: { compact?: boolean }) {
  return (
    <div data-testid="origenlab-logo-static" className="min-w-0">
      <p
        className={`truncate font-bold tracking-tight text-white ${
          compact ? "text-sm" : "text-base"
        }`}
      >
        OrigenLab
      </p>
      <p className={`text-slate-400 ${compact ? "text-[11px]" : "text-xs"}`}>Panel operador</p>
    </div>
  );
}
