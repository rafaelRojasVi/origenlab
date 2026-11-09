/**
 * Module-level store for where the Cotizador should navigate back to after the user presses
 * «← <case>» in the header. Any page that opens the Cotizador and wants a custom back destination
 * calls `setCotizadorReturn(hash)` before navigating; CotizadorPage reads and clears it on mount
 * (via `takeCotizadorReturn`). Paths that don't set it fall back to their usual logic (Pipeline for
 * sales quotes, Licitaciones for tender-offer quotes).
 */

let _returnTo: string | null = null;

/** Set by any page before navigating to the Cotizador that wants a custom back destination. */
export function setCotizadorReturn(hash: string): void {
  _returnTo = hash;
}

/** Read and clear the return destination. Returns null when no caller set it. */
export function takeCotizadorReturn(): string | null {
  const r = _returnTo;
  _returnTo = null;
  return r;
}
