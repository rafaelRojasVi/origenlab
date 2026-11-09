/**
 * Franja «Proveedor inscrito en» (portada y /como-comprar/): el logotipo de
 * cada institución donde OrigenLab está inscrito como proveedor.
 *
 * Nombres y logos aprobados por la dueña el 2026-11-08 (`claims.ts`,
 * `proveedor-inscrito`, campo `names`); `test:message` exige que esta lista y
 * la del registro sean la misma. Ninguna otra institución entra sin permiso
 * escrito. Cada logotipo se descargó del sitio público de su institución:
 *
 * - Laboratorios Saval: https://www.savalcorp.com/skins/responsivo/images/logo.svg (SVG, colores propios)
 * - Universidad Santo Tomás: https://www.ust.cl/web/wp-content/themes/universidad/images/logos/logo-ust-horizontal.svg
 *   (SVG blanco del sitio, pasado a una tinta oscura)
 * - ChileProveedores (Mercado Público): https://www.mercadopublico.cl/Home/Imagenes/NuevoHome/logo-chilecompra-original.png
 *   (chileproveedores.cl no resolvía el 2026-11-08; el registro vive en ChileCompra / Mercado Público)
 * - Corteva Agriscience: https://assets.corteva.com/is/image/Corteva/LOG-Corteva_Logo-Corporate-Global-V1?wid=800&fmt=png-alpha
 *   (el logo corporativo de corteva.com, recortado)
 * - Golden Omega: https://www.goldenomega.cl/wp-content/uploads/2020/07/LogoHeader.png (recortado)
 * - Universidad de Concepción: https://www.udec.cl/app/themes/udec-theme-main/assets/src/images/udec_blanco_horizontal.webp
 *   (logo blanco del sitio, pasado a una tinta oscura)
 */
export interface RegistrationLogo {
  /** Nombre exacto, igual al de `claims.ts`; es también el `alt`. */
  name: string;
  src: string;
  width: number;
  height: number;
}

export const registrationLogos: readonly RegistrationLogo[] = [
  { name: 'Laboratorios Saval', src: '/clientes/laboratorios-saval.svg', width: 416, height: 71 },
  { name: 'Universidad Santo Tomás', src: '/clientes/universidad-santo-tomas.svg', width: 156, height: 48 },
  { name: 'ChileProveedores (Mercado Público)', src: '/clientes/chileproveedores.png', width: 595, height: 131 },
  { name: 'Corteva Agriscience', src: '/clientes/corteva-agriscience.png', width: 400, height: 80 },
  { name: 'Golden Omega', src: '/clientes/golden-omega.png', width: 205, height: 99 },
  { name: 'Universidad de Concepción', src: '/clientes/universidad-de-concepcion.png', width: 560, height: 184 },
];
