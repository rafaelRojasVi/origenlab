/**
 * Etiquetas CTA compartidas en el sitio público. Una por intención.
 *
 * Hay dos intenciones distintas, no dos nombres para la misma. «Solicitar
 * cotización» pide una propuesta formal y es la acción de la cabecera y de la
 * banda de cierre. «Cuéntenos su aplicación» abre la conversación técnica
 * previa, que es lo que la portada y la sección de asesoría ofrecen de verdad:
 * quien todavía no sabe qué equipo necesita no está pidiendo un precio. Ambas
 * llegan a /contacto/.
 */
export const ctaLabels = {
  /** Intención primaria en todo el sitio. */
  solicitarCotizacion: 'Solicitar cotización',
  /** Intención de asesoría previa: portada y sección de asesoría técnica. */
  contarAplicacion: 'Cuéntenos su aplicación',
  /** Forma compacta de la misma intención, sólo en la cabecera estrecha. */
  cotizar: 'Cotizar',
  viewProductDetail: 'Ver ficha',
  email: 'Solicitar por email',
  /** Vocabulario del tablero «Mensaje» (2026-11-07). */
  /** Formulario: «Pedir cotización». */
  pedirCotizacion: 'Pedir cotización',
  /** WhatsApp, ancho normal. Sustituye a «Cotizar por WhatsApp». */
  escribirWhatsapp: 'Escribir por WhatsApp',
  /** WhatsApp con poco ancho. */
  porWhatsapp: 'Por WhatsApp',
  /** WhatsApp junto a otro botón o en una barra fija. */
  whatsappCorto: 'WhatsApp',
  verModelo: 'Ver modelo',
  verEquipos: 'Ver los equipos',
  fichaPdf: 'Ficha técnica del fabricante (PDF)',
  cuentenosAplicacion: 'Cuéntenos su aplicación',
} as const;
