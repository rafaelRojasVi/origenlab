import { contact } from '../data/contact';
import { company } from '../data/company';

export interface NavItem {
  /** Siempre con barra final: `trailingSlash: 'always'` en astro.config.mjs. */
  href: string;
  label: string;
  /** Rutas que marcan este item como activo (además de href). */
  matches?: readonly string[];
}

/**
 * Configuración central del sitio OrigenLab.
 * Descripción corta SEO: derivada de company (evita duplicar narrativa).
 */
export const site = {
  name: 'OrigenLab',
  domain: 'origenlab.cl',
  baseUrl: 'https://origenlab.cl',
  email: contact.email,
  location: contact.locationPublic,
  hours: contact.hours,
  tagline: 'Equipos para laboratorio en todo Chile',
  description: `Venta de equipos para laboratorios de servicio e investigación en ${company.geography}. Alimentos, control de calidad y laboratorio clínico. Cotización por correo o WhatsApp.`,
  /** PNG 1200x630 generado desde el SVG por scripts/build-social-assets.mjs */
  ogImagePath: '/og/origenlab-og.png',
  ogImageAlt: 'OrigenLab, equipos para laboratorio en Chile',
  phone: contact.phoneDisplay,
  whatsapp: contact.phoneDisplay,
  /** Color de la barra del navegador: igual al fondo del header. */
  themeColor: '#fafaf7',
  /**
   * Navegación principal. Cinco destinos y un CTA; no ampliar sin quitar otro.
   * "Aplicaciones" agrupa las rutas /categorias/* existentes (sin migrar URLs).
   */
  nav: [
    { href: '/productos/', label: 'Productos' },
    { href: '/aplicaciones/', label: 'Aplicaciones', matches: ['/categorias/'] },
    { href: '/marcas/', label: 'Marcas' },
    { href: '/servicios/', label: 'Servicios' },
    { href: '/nosotros/', label: 'Nosotros' },
  ] as readonly NavItem[],
  /** Destino único del CTA primario en todo el sitio. */
  quoteHref: '/contacto/',
} as const;
