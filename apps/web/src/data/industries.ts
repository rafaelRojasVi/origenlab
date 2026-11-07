/**
 * Industrias de la portada: nombre corto del tablero HomeA y destino.
 *
 * Cada una lleva a la familia de equipo que más suele traer ese sector (el
 * primer enlace de la antigua sección «A quién atendemos»); el sector público
 * lleva a cómo se cotiza por Mercado Público. Describe el trabajo, no a quién se
 * le ha vendido: sin logotipos de cliente, nombres, testimonios ni conteos.
 */
import { familyHref } from '../lib/modelos';

export const industries: { name: string; href: string }[] = [
  { name: 'Salmón', href: familyHref('sonicacion') },
  { name: 'Alimentos', href: familyHref('pesaje-humedad') },
  { name: 'Farma', href: familyHref('dispersion') },
  { name: 'Universidades', href: familyHref('sonicacion') },
  { name: 'Hospitales', href: familyHref('centrifugacion') },
  { name: 'Sector público', href: '/como-comprar/#compras-publicas' },
];
