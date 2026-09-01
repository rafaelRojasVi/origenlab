export function ReadOnlyBanner({
  mirrorBackend,
  documentImportEnabled = false,
  promotionEnabled = false,
  crmWritesEnabled = false,
}: {
  mirrorBackend: boolean;
  documentImportEnabled?: boolean;
  promotionEnabled?: boolean;
  crmWritesEnabled?: boolean;
}) {
  if (crmWritesEnabled) {
    return (
      <div
        className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700"
        role="note"
      >
        <p className="font-medium text-slate-800">
          Ventas del CRM durable. Los cambios de etapa se registran de forma durable en
          PostgreSQL — no se envían correos ni se modifican cotizaciones.
        </p>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700"
      role="note"
    >
      <p className="font-medium text-slate-800">
        {documentImportEnabled
          ? "Panel comercial de solo lectura. La única escritura habilitada aquí es guardar, de forma explícita, documentos procesados de una licitación."
          : promotionEnabled
            ? "Panel comercial de solo lectura. La única escritura habilitada aquí es promover, de forma explícita, una oportunidad detectada al CRM durable (Ventas)."
            : "Panel de solo lectura. Las decisiones de envío y contacto se toman en el pipeline SQLite y con scripts del operador."}
      </p>
      {mirrorBackend ? (
        <p className="mt-2 text-xs text-slate-500">
          El espejo Postgres no autoriza envíos ni define el estado de contacto.
        </p>
      ) : null}
    </div>
  );
}
