/**
 * Industrias de la portada: nombre corto del tablero HomeA y destino.
 *
 * Cada una lleva a la familia de equipo que más suele traer ese sector (el
 * primer enlace de la antigua sección «A quién atendemos»); el sector público
 * lleva a cómo se cotiza por Mercado Público. Describe el trabajo, no a quién se
 * le ha vendido: sin testimonios ni conteos. Los únicos nombres y logos son los
 * aprobados de la franja «Proveedor inscrito en» (`registrations.ts`).
 */
import { familyHref, type FamilySlug } from '../lib/modelos';

/**
 * `families`: las familias que ese sector suele traer, la primera es la de su
 * enlace. La portada saca de ellas la línea (el nombre de la aplicación que
 * encabeza cada familia, `applications.ts`) y las miniaturas de modelo (orden
 * comercial). Sin familias (sector público), la línea es la afirmación
 * aprobada de Mercado Público.
 */
export interface Industry {
  name: string;
  href: string;
  families: readonly FamilySlug[];
}

const industry = (name: string, families: readonly FamilySlug[], href = familyHref(families[0]!)): Industry => ({
  name,
  href,
  families,
});

export const industries: readonly Industry[] = [
  industry('Salmón', ['sonicacion']),
  industry('Alimentos y agro', ['pesaje-humedad']),
  industry('Farma', ['dispersion']),
  // La investigación académica pasa por las seis familias: tres, una de cada.
  industry('Universidades', ['sonicacion', 'electroforesis', 'osmometria']),
  industry('Hospitales', ['centrifugacion']),
  industry('Sector público', [], '/como-comprar/#compras-publicas'),
];
