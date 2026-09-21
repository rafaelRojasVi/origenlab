/// <reference types="astro/client" />

/**
 * Bandera de vista previa del boletín, inyectada por `astro.config.mjs` desde
 * `ORIGENLAB_NEWSLETTER_PREVIEW`. Es una constante de compilación: en una
 * compilación normal vale `false` y el formulario no llega al HTML.
 */
declare const __ORIGENLAB_NEWSLETTER_PREVIEW__: boolean;
