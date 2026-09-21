/**
 * Normalización de direcciones.
 *
 * La regla es la misma que aplica el esquema del CRM a `value_norm`: minúsculas
 * y una forma comprobable. Que las dos coincidan importa, porque la dirección es
 * la clave por la que un hecho de seguridad sobrevive a cualquier fusión de
 * identidades, y dos normalizaciones distintas producirían dos claves para la
 * misma persona.
 *
 * Lo que **no** se hace: quitar puntos, recortar el sufijo tras un `+` ni
 * ninguna otra normalización específica de un proveedor. Dos direcciones que el
 * proveedor entrega al mismo buzón siguen siendo dos direcciones distintas para
 * su titular, y decidir lo contrario es tratar a alguien como otra persona.
 */

/** Forma aceptada. Deliberadamente idéntica al CHECK del esquema del CRM. */
const SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function normalizeEmail(raw: string): string | null {
  const trimmed = raw.trim().toLowerCase();
  if (trimmed.length === 0 || trimmed.length > 254) return null;
  if (!SHAPE.test(trimmed)) return null;
  /* Un carácter de control en una dirección no es un error de tecleo. */
  if (/[\u0000-\u001f\u007f,;<>"\\]/.test(trimmed)) return null;
  return trimmed;
}

/** Espacios colapsados y recortados. `null` si no queda nada. */
export function normalizeText(raw: string | null, max: number): string | null {
  if (raw === null) return null;
  const value = raw.replace(/\s+/g, ' ').trim();
  if (value.length === 0) return null;
  if (value.length > max) return null;
  return value;
}
