export function ReadOnlyBanner({ mirrorBackend }: { mirrorBackend: boolean }) {
  return (
    <div
      className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700"
      role="note"
    >
      <p className="font-medium text-slate-800">
        Panel de solo lectura. Las decisiones de envío y contacto se toman en el pipeline SQLite y
        con scripts del operador.
      </p>
      {mirrorBackend ? (
        <p className="mt-2 text-xs text-slate-500">
          El espejo Postgres no autoriza envíos ni define el estado de contacto.
        </p>
      ) : null}
    </div>
  );
}
