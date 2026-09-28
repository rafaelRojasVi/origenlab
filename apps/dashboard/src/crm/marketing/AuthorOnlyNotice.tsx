/** What a reader sees if it ever reaches a write surface: nothing to press, and why. */
export function AuthorOnlyNotice() {
  return (
    <p className="rounded-md border border-line bg-canvas-sunken px-3 py-2 text-xs text-ink-muted" data-testid="author-only">
      Sólo lectura: crear, editar, duplicar, congelar o planificar una campaña requiere el rol Ventas o Administración.
    </p>
  );
}
